const {test} = require('tap');
const CarLink = require('../../src/extensions/scratch3_picar/car-link');
const F = require('../fixtures/picar/fakes');

const {FakeWebSocket, welcome, tel, makeLink, connect} = F;

test('url: ?picar_ws override, else same-origin /ws', t => {
    t.equal(CarLink.defaultUrl({protocol: 'http:', host: 'picar.local:8080', search: ''}), 'ws://picar.local:8080/ws');
    t.equal(CarLink.defaultUrl({protocol: 'https:', host: 'x.test', search: '?a=1'}), 'wss://x.test/ws');
    t.equal(CarLink.defaultUrl({protocol: 'http:', host: 'h', search: '?picar_ws=ws%3A%2F%2Flocalhost%3A9%2Fws&x=1'}),
        'ws://localhost:9/ws');
    t.end();
});

test('hello is the first frame and has the documented shape', t => {
    const ctx = makeLink(CarLink);
    t.equal(ctx.link.getState().connection, 'closed');
    ctx.link.start();
    t.equal(ctx.link.getState().connection, 'connecting');
    const ws = FakeWebSocket.last;
    t.equal(ws.url, 'ws://car.local:8080/ws');
    t.equal(ws.sent.length, 0, 'nothing before the socket opens');
    ws.open();
    t.equal(ws.sent.length, 1);
    const hello = ws.sent[0];
    t.equal(hello.t, 'hello');
    t.equal(hello.v, 1);
    t.equal(hello.want, 'control');
    t.equal(hello.mode, 'car');
    t.ok(typeof hello.client_id === 'string' && hello.client_id.length >= 8 && hello.client_id.length <= 64);
    t.notOk('world' in hello);
    t.end();
});

test('hello uses the stored mode (localStorage picar.mode)', t => {
    const ctx = makeLink(CarLink);
    ctx.local.setItem('picar.mode', 'sim');
    const link = new CarLink({url: 'ws://x/ws',
        WebSocketImpl: FakeWebSocket,
        clock: ctx.clock,
        doc: ctx.doc,
        win: ctx.win,
        sessionStore: ctx.session,
        localStore: ctx.local,
        fetchImpl: null,
        random: () => 0});
    link.start();
    FakeWebSocket.last.open();
    t.equal(FakeWebSocket.last.sent[0].mode, 'sim');
    t.equal(link.getState().mode, 'sim');
    t.end();
});

test('welcome opens the link and starts the heartbeat at hb_ms', t => {
    const ctx = makeLink(CarLink);
    const states = [];
    ctx.link.subscribe(s => states.push(s.connection));
    const ws = connect(ctx, {hb_ms: 250});
    const st = ctx.link.getState();
    t.equal(st.connection, 'open');
    t.equal(st.role, 'controller');
    t.equal(st.mode, 'car');
    t.equal(st.carAvailable, true);
    t.equal(st.server.sha, 'abc');
    t.same(st.worlds, [{id: 'arena', title: 'Arena'}]);
    t.same(states, ['connecting', 'open']);
    t.equal(ws.ofType('hb').length, 0);
    ctx.clock.advance(1000);
    t.equal(ws.ofType('hb').length, 4, 'one hb per 250 ms');
    t.ok(Object.isFrozen(st), 'state snapshots are frozen');
    t.end();
});

test('client_id: persisted in the session store, in-memory fallback when storage throws', t => {
    const a = makeLink(CarLink);
    const id = a.link.clientId;
    t.equal(a.session.getItem('picar.clientId'), id);
    const b = new CarLink({WebSocketImpl: FakeWebSocket,
        url: 'ws://x/ws',
        sessionStore: a.session,
        localStore: a.local,
        clock: a.clock,
        fetchImpl: null});
    t.equal(b.clientId, id, 'reload keeps the id');
    const c = new CarLink({WebSocketImpl: FakeWebSocket,
        url: 'ws://x/ws',
        sessionStore: new F.ThrowingStore(),
        localStore: new F.ThrowingStore(),
        clock: a.clock,
        fetchImpl: null});
    t.ok(c.clientId.length >= 8, 'generated without storage');
    c.start();
    FakeWebSocket.last.open();
    t.equal(FakeWebSocket.last.sent[0].client_id, c.clientId);
    t.equal(c.getState().mode, 'car', 'default mode when localStorage throws');
    c.setMode('sim');
    t.equal(c.getState().mode, 'sim', 'in-memory mode');
    t.end();
});

test('command shapes, increasing ids, dedupe and per-op coalescing', t => {
    const ctx = makeLink(CarLink);
    const ws = connect(ctx);
    const id1 = ctx.link.send('drive', {dir: 'forward', speed: 50});
    t.equal(id1, 1);
    t.same(ws.cmds()[0], {t: 'cmd', id: 1, op: 'drive', dir: 'forward', speed: 50});
    t.equal(ctx.link.send('drive', {dir: 'forward', speed: 50}), null, 'identical repeat dropped');
    t.equal(ws.cmds('drive').length, 1);

    // within 50 ms: queued, latest wins
    t.equal(ctx.link.send('drive', {dir: 'forward', speed: 60}), 0);
    t.equal(ctx.link.send('drive', {dir: 'forward', speed: 70}), 0);
    t.equal(ws.cmds('drive').length, 1);
    ctx.clock.advance(49);
    t.equal(ws.cmds('drive').length, 1);
    ctx.clock.advance(1);
    t.equal(ws.cmds('drive').length, 2);
    t.same(ws.cmds('drive')[1], {t: 'cmd', id: 2, op: 'drive', dir: 'forward', speed: 70});

    // ops are independent
    ctx.link.send('steer', {angle: -30});
    ctx.link.send('set_speed', {speed: 30});
    t.same(ws.cmds().slice(2)
        .map(c => c.op), ['steer', 'set_speed']);

    // a pending value equal to what was last sent is not re-sent
    ctx.link.send('steer', {angle: 10});
    ctx.link.send('steer', {angle: -30});
    ctx.clock.advance(100);
    t.equal(ws.cmds('steer').length, 1, 'back to the sent value: nothing to flush');
    t.end();
});

test('identical drive is refreshed after 1 s (server drive timeout needs it)', t => {
    const ctx = makeLink(CarLink);
    const ws = connect(ctx);
    ctx.link.send('drive', {dir: 'forward', speed: 50});
    ctx.clock.advance(900);
    t.equal(ctx.link.send('drive', {dir: 'forward', speed: 50}), null);
    ctx.clock.advance(150);
    t.ok(ctx.link.send('drive', {dir: 'forward', speed: 50}) > 0);
    t.equal(ws.cmds('drive').length, 2);
    t.end();
});

test('config fields merge while pending and identical fields are dropped', t => {
    const ctx = makeLink(CarLink);
    const ws = connect(ctx);
    ctx.link.send('config', {obstacle_stop: false});
    ctx.link.send('config', {stop_cm: 40});
    ctx.link.send('config', {speed_limit: 60});
    ctx.clock.advance(50);
    t.same(ws.cmds('config').map(c => [c.obstacle_stop, c.stop_cm, c.speed_limit]),
        [[false, undefined, undefined], [undefined, 40, 60]]);
    ctx.clock.advance(100);
    t.equal(ctx.link.send('config', {stop_cm: 40}), null);
    t.end();
});

test('stop/estop/pause/resume/move/estop_reset bypass the coalescer and clear pending motion', t => {
    const ctx = makeLink(CarLink);
    const ws = connect(ctx);
    ctx.link.send('drive', {dir: 'forward', speed: 50});
    ctx.link.send('drive', {dir: 'forward', speed: 80});
    ctx.link.send('steer', {angle: 5});
    ctx.link.send('steer', {angle: 9});
    t.equal(ctx.clock.pending > 0, true);
    for (const op of ['stop', 'pause', 'resume', 'estop_reset']) {
        t.ok(ctx.link.send(op) > 0, `${op} sent immediately`);
    }
    ctx.clock.advance(500);
    t.same(ws.cmds().map(c => c.op), ['drive', 'steer', 'stop', 'pause', 'resume', 'estop_reset'],
        'pending drive/steer were cleared, not flushed');
    // after a stop the same drive is a real command again
    ctx.link.send('drive', {dir: 'forward', speed: 50});
    t.equal(ws.cmds('drive').length, 2);
    ctx.link.move('forward', 40, 1000);
    ctx.link.send('drive', {dir: 'forward', speed: 50});
    t.equal(ws.cmds('drive').length, 3, 'move also forgets the drive memory');
    t.end();
});

test('move resolves with done.result', async t => {
    const ctx = makeLink(CarLink);
    const ws = connect(ctx);
    const p = ctx.link.move('backward', 30, 1500);
    t.same(ws.cmds('move')[0], {t: 'cmd', id: 1, op: 'move', dir: 'backward', speed: 30, ms: 1500});
    ws.serverSend({t: 'ack', id: 1, ok: true});
    ws.serverSend({t: 'done', id: 1, result: 'obstacle'});
    t.equal(await p, 'obstacle');
    t.equal(ctx.clock.timers.filter(x => x.due > ctx.clock.t + 1000).length, 0, 'timeout timer cleared');
});

test('move resolves with err.code on err{id}', async t => {
    const ctx = makeLink(CarLink);
    const ws = connect(ctx);
    const p = ctx.link.move('forward', 30, 1500);
    ws.serverSend({t: 'err', id: 1, code: 'hold', msg: 'on hold'});
    t.equal(await p, 'hold');
    t.equal(ctx.link.getState().lastError.code, 'hold');
});

test('move resolves "disconnected" on close, when not open, and on mode switch', async t => {
    const ctx = makeLink(CarLink);
    t.equal(await ctx.link.move('forward', 10, 100), 'disconnected', 'before start');
    const ws = connect(ctx);
    const p = ctx.link.move('forward', 30, 1500);
    ws.serverClose(1006);
    t.equal(await p, 'disconnected');
    ctx.clock.advance(600);
    const w = FakeWebSocket.last;
    w.open();
    w.serverSend(welcome());
    const q = ctx.link.move('forward', 30, 1500);
    ctx.link.setMode('sim');
    t.equal(await q, 'disconnected');
});

test('move resolves "timeout" after ms + 2000 and never rejects', async t => {
    const ctx = makeLink(CarLink);
    const ws = connect(ctx);
    const p = ctx.link.move('forward', 30, 1000);
    for (let i = 0; i < 5; i++) {
        ctx.clock.advance(500);
        ws.serverSend(tel());
    }
    ctx.clock.advance(499);
    let settled = false;
    p.then(() => {
        settled = true;
    });
    await Promise.resolve();
    t.equal(settled, false);
    ctx.clock.advance(1);
    t.equal(await p, 'timeout');
});

test('back-pressure: only stop, estop, pause and hb above 64 KB', t => {
    const ctx = makeLink(CarLink);
    const ws = connect(ctx);
    ws.bufferedAmount = 65 * 1024;
    t.equal(ctx.link.send('drive', {dir: 'forward', speed: 20}), null);
    t.equal(ctx.link.send('steer', {angle: 3}), null);
    t.equal(ctx.link.send('resume'), null);
    t.ok(ctx.link.send('stop') > 0);
    t.ok(ctx.link.send('pause') > 0);
    ctx.link.estop();
    ctx.clock.advance(250);
    t.same(ws.cmds().map(c => c.op), ['stop', 'pause', 'estop']);
    t.ok(ws.ofType('hb').length >= 1, 'hb still flows');
    ws.bufferedAmount = 0;
    t.ok(ctx.link.send('drive', {dir: 'forward', speed: 20}) > 0);
    t.end();
});

test('reconnect backoff 0.5, 1, 2, 4, 5, 5 s and reset after welcome', t => {
    const ctx = makeLink(CarLink);
    const ws = connect(ctx);
    ws.serverClose(1006);
    t.equal(ctx.link.getState().connection, 'closed');
    const expected = [500, 1000, 2000, 4000, 5000, 5000];
    let count = FakeWebSocket.instances.length;
    for (const ms of expected) {
        ctx.clock.advance(ms - 1);
        t.equal(FakeWebSocket.instances.length, count, `not before ${ms}`);
        ctx.clock.advance(1);
        t.equal(FakeWebSocket.instances.length, ++count, `reconnect at ${ms}`);
        t.equal(ctx.link.getState().connection, 'connecting');
        FakeWebSocket.last.serverClose(1006);
    }
    ctx.clock.advance(5000);
    const w = FakeWebSocket.last;
    w.open();
    w.serverSend(welcome());
    w.serverClose(1006);
    ctx.clock.advance(500);
    t.equal(FakeWebSocket.last === w, false, 'welcome reset the backoff to 0.5 s');
    t.end();
});

test('reconnect jitter adds up to 250 ms', t => {
    const ctx = makeLink(CarLink, {random: () => 0.999});
    const ws = connect(ctx);
    const n = FakeWebSocket.instances.length;
    ws.serverClose(1006);
    ctx.clock.advance(740);
    t.equal(FakeWebSocket.instances.length, n);
    ctx.clock.advance(20);
    t.equal(FakeWebSocket.instances.length, n + 1);
    t.end();
});

test('immediate reconnect on visibilitychange->visible, pageshow, online', t => {
    for (const [target, name] of [['doc', 'visibilitychange'], ['win', 'pageshow'], ['win', 'online']]) {
        const ctx = makeLink(CarLink);
        const ws = connect(ctx);
        ws.serverClose(1006);
        const n = FakeWebSocket.instances.length;
        if (name === 'visibilitychange') ctx[target].visibilityState = 'hidden';
        ctx[target].fire(name);
        if (name === 'visibilitychange') {
            t.equal(FakeWebSocket.instances.length, n, 'hidden does not reconnect');
            ctx[target].visibilityState = 'visible';
            ctx[target].fire(name);
        }
        t.equal(FakeWebSocket.instances.length, n + 1, `${name} reconnects immediately`);
        t.equal(ctx.clock.timers.filter(x => !x.every).length, 0, 'backoff timer cancelled');
        ctx[target].fire(name);
        t.equal(FakeWebSocket.instances.length, n + 1, 'no duplicate socket while connecting');
    }
    t.end();
});

test('hidden tab and pagehide send pause', t => {
    const ctx = makeLink(CarLink);
    const ws = connect(ctx);
    ctx.doc.visibilityState = 'hidden';
    ctx.doc.fire('visibilitychange');
    t.same(ws.cmds().map(c => c.op), ['pause']);
    ctx.win.fire('pagehide');
    t.same(ws.cmds().map(c => c.op), ['pause', 'pause']);
    t.end();
});

test('no frame for 2 s while open forces a reconnect', t => {
    const ctx = makeLink(CarLink);
    const ws = connect(ctx);
    const n = FakeWebSocket.instances.length;
    for (let i = 0; i < 3; i++) {
        ctx.clock.advance(600);
        ws.serverSend(tel());
    }
    t.equal(FakeWebSocket.instances.length, n, 'frames keep it alive');
    t.ok(ctx.link.getTelemetry());
    ctx.clock.advance(2600);
    t.equal(ctx.link.getState().connection === 'open', false);
    t.equal(ctx.link.getTelemetry(), null, 'telemetry cleared');
    ctx.clock.advance(600);
    t.equal(FakeWebSocket.instances.length, n + 1);
    t.end();
});

test('close 4400/4409: state reload, no reconnect, even on visibility', t => {
    for (const code of [4409, 4400]) {
        const ctx = makeLink(CarLink);
        const ws = connect(ctx);
        const n = FakeWebSocket.instances.length;
        ws.serverClose(code);
        t.equal(ctx.link.getState().connection, 'reload');
        ctx.clock.advance(60000);
        ctx.doc.fire('visibilitychange');
        ctx.win.fire('online');
        t.equal(FakeWebSocket.instances.length, n, `no reconnect after ${code}`);
    }
    t.end();
});

test('protocol version or build sha mismatch -> reload', t => {
    const a = makeLink(CarLink);
    connect(a, {v: 2});
    t.equal(a.link.getState().connection, 'reload');
    const b = makeLink(CarLink, {buildSha: 'zzz'});
    connect(b);
    t.equal(b.link.getState().connection, 'reload');
    const n = FakeWebSocket.instances.length;
    b.clock.advance(60000);
    t.equal(FakeWebSocket.instances.length, n);
    const c = makeLink(CarLink, {buildSha: 'abc'});
    connect(c);
    t.equal(c.link.getState().connection, 'open', 'same sha is fine');
    t.end();
});

test('estop while closed is queued once, posted over REST, and sent right after welcome', t => {
    const ctx = makeLink(CarLink);
    ctx.link.estop();
    ctx.link.estop();
    t.equal(ctx.fetches.length, 2);
    t.equal(ctx.fetches[0].url, 'http://car.local:8080/api/estop');
    t.equal(ctx.fetches[0].opts.method, 'POST');
    t.equal(ctx.fetches[0].opts.keepalive, true);
    const ws = connect(ctx);
    t.same(ws.cmds().map(c => c.op), ['estop'], 'one queued estop');
    ws.serverClose(1006);
    ctx.clock.advance(600);
    const ws2 = FakeWebSocket.last;
    ws2.open();
    ws2.serverSend(welcome());
    t.equal(ws2.cmds('estop').length, 0, 'not re-sent on the next reconnect');
    t.end();
});

test('estop while open goes over the socket and REST in parallel', t => {
    const ctx = makeLink(CarLink);
    const ws = connect(ctx);
    ctx.link.send('drive', {dir: 'forward', speed: 50});
    ctx.link.send('drive', {dir: 'forward', speed: 60});
    ctx.link.estop();
    t.same(ws.cmds().map(c => c.op), ['drive', 'estop']);
    t.equal(ctx.fetches.length, 1);
    ctx.clock.advance(200);
    t.equal(ws.cmds('drive').length, 1, 'pending drive cleared');
    t.end();
});

test('estop from a socket that is open but not welcomed yet still goes out', t => {
    const ctx = makeLink(CarLink);
    ctx.link.start();
    const ws = FakeWebSocket.last;
    ws.open();
    ctx.link.estop();
    t.equal(ws.cmds('estop').length, 1);
    t.end();
});

test('motion is never replayed after a reconnect', t => {
    const ctx = makeLink(CarLink);
    const ws = connect(ctx);
    ctx.link.send('drive', {dir: 'forward', speed: 50});
    ctx.link.send('drive', {dir: 'forward', speed: 60});
    ctx.link.send('steer', {angle: 20});
    ws.serverClose(1006);
    ctx.clock.advance(600);
    const ws2 = FakeWebSocket.last;
    ws2.open();
    ws2.serverSend(welcome());
    ctx.clock.advance(500);
    t.same(ws2.cmds(), []);
    t.same(ws2.sent.map(m => m.t).filter(x => x !== 'hb'), ['hello']);
    ctx.link.send('drive', {dir: 'forward', speed: 50});
    t.equal(ws2.cmds()[0].id, 1, 'ids restart per connection');
    t.end();
});

test('setMode: switching drops motion until the fresh welcome; telemetry cleared', t => {
    const ctx = makeLink(CarLink);
    const ws = connect(ctx);
    ws.serverSend(tel());
    t.ok(ctx.link.getTelemetry());
    ctx.link.setMode('sim', 'corridor');
    t.equal(ctx.local.getItem('picar.mode'), 'sim');
    t.equal(ctx.link.getState().connection, 'switching');
    t.equal(ctx.link.getTelemetry(), null);
    t.same(ws.sent[ws.sent.length - 1], {t: 'mode', mode: 'sim', world: 'corridor'});
    t.equal(ctx.link.send('drive', {dir: 'forward', speed: 50}), null, 'motion dropped');
    t.ok(ctx.link.send('stop') > 0, 'stop still goes out');
    ws.serverSend(tel());
    t.equal(ctx.link.getTelemetry(), null, 'stale tel ignored while switching');
    ws.serverSend(welcome({mode: 'sim'}));
    t.equal(ctx.link.getState().connection, 'open');
    t.equal(ctx.link.getState().mode, 'sim');
    ws.serverSend({t: 'world', id: 'corridor', walls: []});
    t.equal(ctx.link.getState().world.id, 'corridor');
    t.ok(ctx.link.send('drive', {dir: 'forward', speed: 50}) > 0);
    t.end();
});

test('setMode refused by the server (sim_busy) falls back to the previous mode', t => {
    const ctx = makeLink(CarLink);
    const ws = connect(ctx);
    ctx.link.setMode('sim');
    ws.serverSend({t: 'err', code: 'sim_busy', msg: 'full'});
    const st = ctx.link.getState();
    t.equal(st.connection, 'open');
    t.equal(st.mode, 'car');
    t.equal(ctx.local.getItem('picar.mode'), 'car');
    t.end();
});

test('car_available:false switches to sim once', t => {
    const ctx = makeLink(CarLink);
    const ws = connect(ctx, {car_available: false});
    t.same(ws.sent[ws.sent.length - 1], {t: 'mode', mode: 'sim'});
    t.equal(ctx.link.getState().connection, 'switching');
    ws.serverSend(welcome({mode: 'sim', car_available: false}));
    t.equal(ctx.link.getState().connection, 'open');
    ctx.link.setMode('car');
    ws.serverSend(welcome({mode: 'car', car_available: false}));
    t.equal(ctx.link.getState().mode, 'car', 'not forced to sim a second time');
    t.equal(ctx.link.getState().connection, 'open');
    t.end();
});

test('events: telemetry, event, world, role, state; never "error"', t => {
    const ctx = makeLink(CarLink);
    const seen = {telemetry: 0, event: [], role: [], protocol_error: []};
    ctx.link.on('telemetry', () => seen.telemetry++);
    ctx.link.on('event', e => seen.event.push(e.kind));
    ctx.link.on('role', r => seen.role.push(r.role));
    ctx.link.on('protocol_error', e => seen.protocol_error.push(e.reason));
    ctx.link.on('error', () => t.fail('error must never be emitted'));
    const ws = connect(ctx);
    ws.serverSend(tel());
    ws.serverSend({t: 'event', kind: 'obstacle_stop', cm: 12});
    ws.serverSend({t: 'event', kind: 'estop', by: 'x'});
    ws.serverSend({t: 'role', role: 'viewer', controller_present: true});
    ws.serverSend('not json {');
    ws.serverSend({nope: 1});
    ws.serverSend({t: 'mystery'});
    ws.onmessage({data: 42});
    ws.onerror({});
    t.equal(seen.telemetry, 1);
    t.same(seen.event, ['obstacle_stop', 'estop']);
    t.same(seen.role, ['viewer']);
    t.same(seen.protocol_error, ['bad_json', 'bad_frame', 'unknown_type', 'not_text']);
    t.equal(ctx.link.getState().role, 'viewer');
    t.equal(ctx.link.getState().safety.estop, true);
    t.equal(ctx.link.listenerCount('error'), 1, 'only our test listener');
    t.end();
});

test('subscribe fires on coarse changes only, not on every tel; unsubscribe works', t => {
    const ctx = makeLink(CarLink);
    const ws = connect(ctx);
    let n = 0;
    const off = ctx.link.subscribe(() => n++);
    ws.serverSend(tel());
    const after = n;
    ws.serverSend(tel({seq: 2, dist: {cm: 30, state: 'ok'}}));
    ws.serverSend(tel({seq: 3, dist: {cm: 29, state: 'ok'}}));
    t.equal(n, after, 'distance changes do not notify');
    ws.serverSend(tel({seq: 4, safety: Object.assign({}, tel().safety, {estop: true})}));
    t.equal(n, after + 1);
    off();
    ws.serverClose(1006);
    t.equal(n, after + 1);
    t.end();
});

test('control sends take/release/force; viewer-only frames are not invented', t => {
    const ctx = makeLink(CarLink);
    const ws = connect(ctx, {role: 'viewer'});
    ctx.link.control('take', true);
    ctx.link.control('release');
    t.same(ws.ofType('control'), [{t: 'control', action: 'take', force: true}, {t: 'control', action: 'release'}]);
    t.end();
});

test('stop() closes without reconnecting and sends a final stop', t => {
    const ctx = makeLink(CarLink);
    const ws = connect(ctx);
    const n = FakeWebSocket.instances.length;
    ctx.link.stop();
    t.same(ws.cmds().map(c => c.op), ['stop']);
    t.equal(ws.closedWith, 1000);
    t.equal(ctx.link.getState().connection, 'closed');
    ctx.clock.advance(60000);
    ctx.doc.fire('visibilitychange');
    t.equal(FakeWebSocket.instances.length, n);
    t.equal(ctx.clock.pending, 0);
    t.end();
});
