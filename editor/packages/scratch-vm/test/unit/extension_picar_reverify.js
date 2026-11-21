const {test} = require('tap');
const PiCar = require('../../src/extensions/scratch3_picar');
const CarLink = require('../../src/extensions/scratch3_picar/car-link');
const F = require('../fixtures/picar/fakes');

const {FakeRuntime, welcome, tel, makeLink, connect} = F;

const setup = (welcomeOverrides = {safety: {estop: false, hold: false}}, linkOptions) => {
    const ctx = makeLink(CarLink, linkOptions);
    const runtime = new FakeRuntime();
    runtime.log = [];
    const stopAll = runtime.stopAll.bind(runtime);
    const startHats = runtime.startHats.bind(runtime);
    runtime.stopAll = () => {
        runtime.log.push('stopAll');
        return stopAll();
    };
    runtime.startHats = (opcode, fields) => {
        runtime.log.push(`hat:${fields.EVENT}`);
        return startHats(opcode, fields);
    };
    runtime.picarLinkFactory = () => ctx.link;
    ctx.ext = new PiCar(runtime);
    ctx.runtime = runtime;
    ctx.ws = connect(ctx, welcomeOverrides);
    return ctx;
};

const estopTel = on => tel({safety: Object.assign({}, tel().safety, {estop: on})});

test('a second E-STOP after a reset within 50 ms still goes out (frame and REST)', t => {
    const {link, ws, clock, fetches} = setup({role: 'viewer', safety: {estop: false, hold: false}});
    link.estop();
    clock.advance(10);
    ws.serverSend({t: 'event', kind: 'estop_reset', by: 'controller'});
    link.estop();
    t.equal(ws.cmds('estop').length, 2);
    t.equal(fetches.length, 2);
    t.end();
});

test('a tel that flips safety.estop also invalidates the suppression memory', t => {
    const {link, ws, clock, fetches} = setup({role: 'viewer', safety: {estop: false, hold: false}});
    link.estop();
    clock.advance(10);
    ws.serverSend(estopTel(true));
    ws.serverSend(estopTel(false));
    link.estop();
    t.equal(ws.cmds('estop').length, 2);
    t.equal(fetches.length, 2);
    t.end();
});

test('a throttled E-STOP REST post is sent once at the end of the window', t => {
    const {link, clock, fetches} = setup();
    link.estop();
    clock.advance(100);
    link.estop();
    link.estop();
    t.equal(fetches.length, 1);
    clock.advance(149);
    t.equal(fetches.length, 1, 'not before the window ends');
    clock.advance(2);
    t.equal(fetches.length, 2, 'exactly one trailing post');
    clock.advance(1000);
    t.equal(fetches.length, 2);
    t.end();
});

test('resume and estop_reset are never de-duplicated', t => {
    const {link, ws, clock} = setup();
    link.send('resume');
    link.send('pause');
    clock.advance(5);
    link.send('stop');
    link.send('resume');
    t.equal(ws.cmds('resume').length, 2, 'resume -> pause -> stop -> resume');
    link.send('estop_reset');
    link.send('estop');
    link.send('estop_reset');
    t.equal(ws.cmds('estop_reset').length, 2);
    t.end();
});

test('local E-STOP then the server echo: the when-estop hat is not killed (event first)', t => {
    const {ws, runtime, link} = setup();
    runtime.emit('PROJECT_START');
    link.estop();
    ws.serverSend({t: 'event', kind: 'estop', by: 'controller'});
    ws.serverSend(estopTel(true));
    t.same(runtime.log.filter(x => x !== 'hat:obstacle'), ['stopAll', 'hat:estop']);
    t.end();
});

test('local E-STOP then the server echo: the when-estop hat is not killed (tel first)', t => {
    const {ws, runtime, link} = setup();
    runtime.emit('PROJECT_START');
    link.estop();
    ws.serverSend(estopTel(true));
    ws.serverSend({t: 'event', kind: 'estop', by: 'controller'});
    t.same(runtime.log, ['stopAll', 'hat:estop']);
    t.end();
});

test('after a local E-STOP is reset, a remote E-STOP still stops the project', t => {
    const {ws, runtime, link} = setup();
    runtime.emit('PROJECT_START');
    link.estop();
    ws.serverSend(estopTel(true));
    ws.serverSend(estopTel(false));
    runtime.emit('PROJECT_START');
    runtime.log.length = 0;
    ws.serverSend(estopTel(true));
    t.same(runtime.log, ['stopAll', 'hat:estop']);
    t.end();
});

test('a backwards clock step does not suppress later presses', t => {
    const {link, ws, clock, fetches} = setup({role: 'viewer', safety: {estop: false, hold: false}});
    link.estop();
    clock.t -= 60000;
    link.estop();
    t.equal(ws.cmds('estop').length, 2);
    t.equal(fetches.length, 2);
    t.end();
});

test('the default clock is monotonic', t => {
    const origNow = Date.now;
    const link = new CarLink({
        url: 'ws://x/ws',
        WebSocketImpl: class {},
        doc: null,
        win: null,
        sessionStore: new F.FakeStore(),
        localStore: new F.FakeStore()
    });
    Date.now = () => 1; // would be a wall-clock jump
    t.ok(link._clock.now() > 1 || typeof performance === 'undefined');
    Date.now = origNow;
    t.end();
});

test('build sha comparison tolerates a longer or shorter form of the same commit', t => {
    const run = (server, build) => {
        const c = makeLink(CarLink, {buildSha: build});
        c.link.start();
        const ws = F.FakeWebSocket.last;
        ws.open();
        ws.serverSend(welcome({server: {sha: server}}));
        return c.link.getState().connection;
    };
    t.equal(run('0123456789ab', '0123456789abcdef0123'), 'open');
    t.equal(run('0123456789ab', '0123456'), 'open');
    t.equal(run('0123456789ab', '0123457'), 'reload');
    t.equal(run('abc', 'abc'), 'open', 'identical short shas match');
    t.end();
});

test('drive, drive, set_speed within one window ends at the last speed', t => {
    const {ext, ws, clock} = setup();
    ext.drive({DIR: 'forward', SPEED: 50});
    ext.drive({DIR: 'backward', SPEED: 80});
    ext.setSpeed({SPEED: 20});
    clock.advance(200);
    const sent = ws.cmds().filter(c => c.op === 'drive' || c.op === 'set_speed');
    const last = sent[sent.length - 1];
    t.equal(last.speed, 20);
    t.equal(last.op, 'drive', 'folded into the pending drive');
    t.equal(last.dir, 'backward');
    t.equal(ws.cmds('set_speed').length, 0);
    t.end();
});

test('a pending set_speed does not override a later drive', t => {
    const {link, ws, clock} = setup();
    link.send('drive', {dir: 'forward', speed: 50});
    link.send('set_speed', {speed: 20});
    link.send('set_speed', {speed: 30});
    link.send('drive', {dir: 'forward', speed: 80});
    clock.advance(200);
    const sent = ws.cmds().filter(c => c.op === 'drive' || c.op === 'set_speed');
    t.equal(sent[sent.length - 1].speed, 80);
    t.equal(sent[sent.length - 1].op, 'drive');
    t.end();
});

test('a reset cancels a trailing E-STOP post still waiting in its throttle window', t => {
    const {link, clock, fetches} = setup();
    link.estop();
    clock.advance(100);
    link.estop();
    t.equal(fetches.length, 1, 'second press is throttled into a trailing post');
    link.send('estop_reset');
    clock.advance(1000);
    t.equal(fetches.length, 1, 'the trailing post must not re-latch after the reset');
    t.end();
});

test('a server estop_reset event also cancels a pending trailing E-STOP post', t => {
    const {link, ws, clock, fetches} = setup({role: 'viewer', safety: {estop: false, hold: false}});
    link.estop();
    clock.advance(100);
    link.estop();
    ws.serverSend({t: 'event', kind: 'estop_reset', by: 'controller'});
    clock.advance(1000);
    t.equal(fetches.length, 1);
    t.end();
});
