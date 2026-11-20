const {test} = require('tap');
const PiCar = require('../../src/extensions/scratch3_picar');
const CarLink = require('../../src/extensions/scratch3_picar/car-link');
const F = require('../fixtures/picar/fakes');

const {FakeWebSocket, FakeRuntime, welcome, tel, makeLink, connect} = F;

const setup = (welcomeOverrides = {safety: {estop: false, hold: false}}, linkOptions) => {
    const ctx = makeLink(CarLink, linkOptions);
    const runtime = new FakeRuntime();
    runtime.picarLinkFactory = () => ctx.link;
    ctx.ext = new PiCar(runtime);
    ctx.runtime = runtime;
    ctx.ws = connect(ctx, welcomeOverrides);
    return ctx;
};

test('repeated "set speed 0" is sent as a stop every time', t => {
    const {ext, ws, clock} = setup();
    // The car keeps talking, so the link stays alive.
    const wait = ms => {
        for (let left = ms; left > 0; left -= 500) {
            clock.advance(Math.min(500, left));
            ws.serverSend(tel());
        }
    };
    ext.drive({DIR: 'forward', SPEED: 50});
    wait(500);
    ext.setSpeed({SPEED: 0});
    wait(500);
    ext.drive({DIR: 'forward', SPEED: 60});
    wait(500);
    ext.setSpeed({SPEED: 0});
    wait(5000);
    ext.setSpeed({SPEED: 0});
    wait(200);
    t.equal(ws.cmds('stop').length, 3, 'every spaced-out set speed 0 reaches the car');
    t.equal(ws.cmds('set_speed').length, 0, 'a zero speed travels as stop');
    t.end();
});

test('set speed 0 and drive at 0 go out under back-pressure', t => {
    const {link, ws, clock} = setup();
    link.send('drive', {dir: 'forward', speed: 50});
    clock.advance(500);
    ws.bufferedAmount = 10 * 1024 * 1024;
    t.ok(link.send('set_speed', {speed: 0}) > 0);
    clock.advance(500);
    t.ok(link.send('drive', {dir: 'forward', speed: 0}) > 0);
    t.equal(ws.cmds('stop').length, 2);
    t.end();
});

test('drive then set_speed then drive at the same speed re-sends the drive', t => {
    const {ext, ws, clock} = setup();
    ext.drive({DIR: 'forward', SPEED: 20});
    clock.advance(100);
    ext.setSpeed({SPEED: 80});
    clock.advance(100);
    ext.drive({DIR: 'forward', SPEED: 20});
    clock.advance(100);
    t.equal(ws.cmds('drive').length, 2);
    t.end();
});

test('a repeat set_speed after a drive is re-sent (drive clears the memory)', t => {
    const {ext, ws, clock} = setup();
    ext.setSpeed({SPEED: 40});
    clock.advance(100);
    ext.drive({DIR: 'forward', SPEED: 10});
    clock.advance(100);
    ext.setSpeed({SPEED: 40});
    clock.advance(100);
    t.equal(ws.cmds('set_speed').length, 2);
    t.end();
});

test('E-STOP stops the project locally even when the socket is closed and the server never answers', t => {
    const {ws, clock, runtime, link} = setup();
    runtime.emit('PROJECT_START');
    ws.serverClose(1006);
    link.estop();
    t.equal(runtime.stopAllCalls, 1, 'stopAll at once');
    t.equal(runtime.hats.filter(h => h.fields.EVENT === 'estop').length, 1, 'when-estop hat still starts');
    clock.advance(600);
    const ws2 = FakeWebSocket.last;
    ws2.open();
    ws2.serverSend(welcome({safety: {estop: true, hold: true}}));
    t.ok(runtime.stopAllCalls >= 2, 'the reconnect welcome stops the project too');
    t.end();
});

test('a tel frame that flips estop or hw_fault to true stops the project', t => {
    const {ws, runtime} = setup();
    runtime.emit('PROJECT_START');
    ws.serverSend(tel({safety: Object.assign({}, tel().safety, {estop: true, hold: true})}));
    t.equal(runtime.stopAllCalls, 1, 'estop');
    runtime.emit('PROJECT_START');
    ws.serverSend(tel({safety: Object.assign({}, tel().safety, {estop: true, hold: true, hw_fault: 'motor'})}));
    t.equal(runtime.stopAllCalls, 2, 'hw_fault');
    t.end();
});

test('a viewer ignores another client estop for its project, but its own E-STOP stops it', t => {
    const {ws, runtime, link} = setup({role: 'viewer', safety: {estop: false, hold: false}});
    runtime.emit('PROJECT_START');
    ws.serverSend(tel({ctl: {role: 'viewer', viewers: 1}, safety: Object.assign({}, tel().safety, {estop: true})}));
    t.equal(runtime.stopAllCalls, 0);
    link.estop();
    t.equal(runtime.stopAllCalls, 1);
    t.end();
});

test('acquiring control or switching to the car stops the running project', t => {
    const {ws, runtime, link} = setup({mode: 'sim', safety: {estop: false, hold: false}});
    runtime.emit('PROJECT_START');
    link.setMode('car');
    t.equal(runtime.stopAllCalls, 1, 'sim to car');
    ws.serverSend(welcome({mode: 'car', role: 'viewer', safety: {estop: false, hold: false}}));
    runtime.emit('PROJECT_START');
    ws.serverSend({t: 'role', role: 'controller'});
    t.equal(runtime.stopAllCalls, 2, 'viewer to controller');
    t.end();
});

test('a reconnect welcome stops the running project; the first welcome does not', t => {
    const ctx = makeLink(CarLink);
    const runtime = new FakeRuntime();
    runtime.picarLinkFactory = () => ctx.link;
    ctx.ext = new PiCar(runtime);
    const ws = connect(ctx, {safety: {estop: false, hold: false}});
    t.equal(runtime.stopAllCalls, 0);
    runtime.emit('PROJECT_START');
    ws.serverClose(1006);
    ctx.clock.advance(600);
    const ws2 = FakeWebSocket.last;
    ws2.open();
    ws2.serverSend(welcome({safety: {estop: false, hold: false}}));
    t.equal(runtime.stopAllCalls, 1);
    t.end();
});

test('welcome while the page is hidden sends pause', t => {
    const {ws, clock, doc} = setup();
    ws.serverClose(1006);
    doc.visibilityState = 'hidden';
    doc.fire('visibilitychange');
    clock.advance(600);
    const ws2 = FakeWebSocket.last;
    ws2.open();
    ws2.serverSend(welcome({safety: {estop: false, hold: false}}));
    t.equal(ws2.cmds('pause').length, 1);
    t.end();
});

test('an err carrying an id while switching is a command error, not a refused switch', t => {
    const {ws, link} = setup();
    link.setMode('sim');
    const id = link.send('stop');
    ws.serverSend({t: 'err', id, code: 'rate_limited', msg: 'x'});
    t.equal(link.getState().connection, 'switching');
    ws.serverSend({t: 'err', code: 'sim_busy', msg: 'busy'});
    t.equal(link.getState().connection, 'open', 'an id-less err is the refusal');
    t.equal(link.getState().mode, 'car');
    t.end();
});

test('the automatic fallback to the simulator is not persisted', t => {
    const ctx = makeLink(CarLink);
    connect(ctx, {car_available: false, mode: 'car'});
    t.equal(ctx.link.getState().mode, 'sim', 'switching to sim');
    t.equal(ctx.local.getItem('picar.mode'), null);
    ctx.link.setMode('car');
    t.equal(ctx.local.getItem('picar.mode'), 'car', 'an explicit choice still persists');
    t.end();
});

test('build sha: compared only when both are known and neither is dev', t => {
    const mismatch = (build, server) => {
        const ctx = makeLink(CarLink, {buildSha: build});
        connect(ctx, {server: {version: '1', sha: server}});
        return ctx.link.getState().connection === 'reload';
    };
    t.equal(mismatch('aaa', 'bbb'), true);
    t.equal(mismatch('aaa', 'aaa'), false);
    t.equal(mismatch('dev', 'bbb'), false);
    t.equal(mismatch('aaa', 'dev'), false);
    t.equal(mismatch(null, 'bbb'), false);
    t.equal(mismatch('aaa', null), false);
    t.end();
});

test('stop motors in a tight loop does not flood the socket', t => {
    const {ext, ws, clock} = setup();
    ws.bufferedAmount = 10 * 1024 * 1024;
    for (let i = 0; i < 1000; i++) ext.stopMotors();
    t.equal(ws.cmds('stop').length, 1, 'identical stops within 50 ms collapse');
    clock.advance(60);
    ext.stopMotors();
    t.equal(ws.cmds('stop').length, 2);
    ws.bufferedAmount = 0;
    ext.link.send('resume');
    ext.link.send('stop');
    t.equal(ws.cmds('stop').length, 3, 'something else in between lets the next stop through');
    t.end();
});

test('E-STOP presses are throttled: REST at most every 250 ms, the frame at most every 50 ms', t => {
    const {ws, clock, link, fetches} = setup();
    for (let i = 0; i < 100; i++) link.estop();
    t.equal(ws.cmds('estop').length, 1);
    t.equal(fetches.length, 1);
    clock.advance(60);
    link.estop();
    t.equal(ws.cmds('estop').length, 2);
    t.equal(fetches.length, 1, 'REST still throttled');
    clock.advance(250);
    link.estop();
    t.equal(fetches.length, 2);
    t.end();
});
