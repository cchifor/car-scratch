const {test} = require('tap');
const PiCar = require('../../src/extensions/scratch3_picar');
const CarLink = require('../../src/extensions/scratch3_picar/car-link');
const BlockType = require('../../src/extension-support/block-type');
const F = require('../fixtures/picar/fakes');

const {FakeWebSocket, FakeRuntime, welcome, tel, makeLink, connect} = F;

/**
 * Build the extension over a real CarLink on fakes.
 * @param {object} [welcomeOverrides] - welcome overrides; null leaves the link unconnected
 * @returns {object} context
 */
const setup = welcomeOverrides => {
    const ctx = makeLink(CarLink);
    const runtime = new FakeRuntime();
    runtime.picarLinkFactory = () => ctx.link;
    const ext = new PiCar(runtime);
    ctx.runtime = runtime;
    ctx.ext = ext;
    if (welcomeOverrides !== null) ctx.ws = connect(ctx, welcomeOverrides);
    return ctx;
};

const ARM = ctx => ctx.runtime.emit('PROJECT_START');

test('registers as a lookup-only peripheral and exposes link', t => {
    const ctx = setup(null);
    t.equal(ctx.runtime.peripheralExtensions.picar, ctx.ext);
    t.equal(ctx.ext.link, ctx.link);
    t.equal(FakeWebSocket.instances.length, 1, 'link started');
    t.equal(ctx.ext.isConnected(), false);
    t.doesNotThrow(() => {
        ctx.ext.scan();
        ctx.ext.connect();
        ctx.ext.disconnect();
    });
    FakeWebSocket.last.open();
    FakeWebSocket.last.serverSend(welcome());
    t.equal(ctx.ext.isConnected(), true);
    t.end();
});

test('no WebSocket global and no factory: the extension still loads, inert', t => {
    const saved = globalThis.WebSocket;
    delete globalThis.WebSocket;
    try {
        const rt = new FakeRuntime();
        const ext = new PiCar(rt);
        t.equal(ext.link, null);
        t.equal(ext.distance(), 999);
        t.equal(ext.isConnected(), false);
        t.doesNotThrow(() => ext.drive({DIR: 'forward', SPEED: 50}));
        t.equal(ext.driveFor({DIR: 'forward', SPEED: 50, SECS: 1}), undefined);
    } finally {
        if (saved !== undefined) globalThis.WebSocket = saved;
    }
    t.end();
});

test('?picar_debug=1 exposes window.__picar = {link, runtime}', t => {
    const savedLoc = Object.getOwnPropertyDescriptor(globalThis, 'location');
    Object.defineProperty(globalThis, 'location', {value: {search: '?picar_debug=1'}, configurable: true});
    try {
        const ctx = setup(null);
        t.equal(globalThis.__picar.link, ctx.link);
        t.equal(globalThis.__picar.runtime, ctx.runtime);
    } finally {
        delete globalThis.__picar;
        if (savedLoc) Object.defineProperty(globalThis, 'location', savedLoc);
        else delete globalThis.location;
    }
    t.end();
});

test('getInfo: ids, colours, opcodes have functions, block types', t => {
    const ctx = setup(null);
    const info = ctx.ext.getInfo();
    t.equal(info.id, 'picar');
    t.equal(info.name, 'Pi Car');
    t.equal(info.color1, '#C62828');
    t.match(info.blockIconURI, /^data:image\/svg\+xml;base64,/);
    t.match(info.id, /^[a-z0-9]+$/i);
    const blocks = info.blocks.filter(b => typeof b === 'object');
    const expectedOps = ['drive', 'driveFor', 'stopMotors', 'setSpeed', 'steer', 'setSteering', 'distance',
        'obstacleCloser', 'whenObstacle', 'lineValue', 'lineSees', 'linePosition', 'whenLineSees', 'lightValue',
        'brightestDirection', 'setObstacleStop', 'setStopDistance', 'setSpeedLimit', 'emergencyStop', 'isConnected',
        'carMode', 'carX', 'carY', 'carDirection', 'resetPosition', 'whenCarEvent'];
    t.same(blocks.map(b => b.opcode), expectedOps);
    const argNames = {};
    for (const b of blocks) {
        t.match(b.opcode, /^[a-z0-9]+$/i);
        t.equal(typeof ctx.ext[b.opcode], 'function', `${b.opcode} has a function`);
        const used = (b.text.match(/\[([A-Z]+)\]/g) || []).map(s => s.slice(1, -1));
        t.same(Object.keys(b.arguments || {}).sort(), used.slice().sort(), `${b.opcode} text matches arguments`);
        argNames[b.opcode] = b.arguments || {};
    }
    const type = op => blocks.find(b => b.opcode === op).blockType;
    t.equal(type('drive'), BlockType.COMMAND);
    t.equal(type('driveFor'), BlockType.COMMAND);
    t.equal(type('distance'), BlockType.REPORTER);
    t.equal(type('obstacleCloser'), BlockType.BOOLEAN);
    t.equal(type('lineSees'), BlockType.BOOLEAN);
    t.equal(type('isConnected'), BlockType.BOOLEAN);
    t.equal(type('whenObstacle'), BlockType.HAT);
    t.equal(type('whenLineSees'), BlockType.HAT);
    t.equal(type('whenCarEvent'), BlockType.HAT);
    const hat = op => blocks.find(b => b.opcode === op);
    t.equal(hat('whenObstacle').isEdgeActivated, true);
    t.equal(hat('whenLineSees').isEdgeActivated, true);
    t.equal(hat('whenCarEvent').isEdgeActivated, false);
    t.equal(hat('whenCarEvent').shouldRestartExistingThreads, false);

    t.equal(argNames.drive.SPEED.defaultValue, 50);
    t.equal(argNames.drive.DIR.defaultValue, 'forward');
    t.equal(argNames.driveFor.SECS.defaultValue, 1);
    t.equal(argNames.steer.STEER.defaultValue, 'straight');
    t.equal(argNames.setSteering.ANGLE.defaultValue, 0);
    t.equal(argNames.obstacleCloser.CM.defaultValue, 20);
    t.equal(argNames.whenObstacle.CM.defaultValue, 20);
    t.equal(String(argNames.lineValue.SENSOR.defaultValue), '1');
    t.equal(String(argNames.lineSees.SENSOR.defaultValue), '3');
    t.equal(String(argNames.whenLineSees.SENSOR.defaultValue), '3');
    t.equal(argNames.lightValue.LIGHT.defaultValue, 'middle');
    t.equal(argNames.setObstacleStop.ONOFF.defaultValue, 'on');
    t.equal(argNames.setStopDistance.CM.defaultValue, 20);
    t.equal(argNames.setSpeedLimit.PERCENT.defaultValue, 100);
    t.equal(argNames.whenCarEvent.EVENT.defaultValue, 'obstacle');

    const menuValues = name => info.menus[name].items.map(i => i.value);
    t.same(menuValues('dir'), ['forward', 'backward']);
    t.same(menuValues('steer'), ['left', 'straight', 'right']);
    t.same(menuValues('sensor'), ['1', '2', '3', '4', '5']);
    t.same(menuValues('light'), ['left', 'middle', 'right']);
    t.same(menuValues('onoff'), ['on', 'off']);
    t.same(menuValues('event'), ['obstacle', 'estop', 'disconnected']);
    t.equal(info.menus.sensor.acceptReporters, true);
    for (const m of ['dir', 'steer', 'light', 'onoff', 'event']) {
        t.notOk(info.menus[m].acceptReporters, `${m} is a plain field menu`);
    }
    t.same(info.menus.event.items.map(i => i.text),
        ['stops for an obstacle', 'is emergency stopped', 'loses connection']);
    // Every menu referenced exists.
    for (const b of blocks) {
        for (const a of Object.values(b.arguments || {})) {
            if (a.menu) t.ok(info.menus[a.menu], `${b.opcode} menu ${a.menu}`);
        }
    }
    t.end();
});

test('command blocks send the documented messages', t => {
    const ctx = setup();
    const {ext, ws, clock} = ctx;
    const last = () => ws.cmds()[ws.cmds().length - 1];
    const next = () => clock.advance(100);

    ext.drive({DIR: 'forward', SPEED: 50});
    t.same(last(), {t: 'cmd', id: 1, op: 'drive', dir: 'forward', speed: 50});
    next();
    ext.drive({DIR: 'backward', SPEED: '30'});
    t.same(last(), {t: 'cmd', id: 2, op: 'drive', dir: 'backward', speed: 30});
    next();
    ext.drive({DIR: 'forward', SPEED: 500});
    t.equal(last().speed, 100, 'clamped');
    next();
    ext.drive({DIR: 'forward', SPEED: 'abc'});
    t.equal(last().op, 'stop', 'NaN -> 0 -> stop');
    next();
    ext.drive({DIR: 'forward', SPEED: 0});
    t.equal(last().op, 'stop', 'speed 0 = stop');
    ext.drive({DIR: 'forward', SPEED: -Infinity});
    t.equal(last().op, 'stop');
    next();
    ext.drive({DIR: 'forward', SPEED: Infinity});
    t.equal(last().speed, 100);
    next();
    ext.stopMotors();
    t.same(last(), {t: 'cmd', id: last().id, op: 'stop'});
    next();
    ext.setSpeed({SPEED: 70});
    t.same(last(), {t: 'cmd', id: last().id, op: 'set_speed', speed: 70});
    next();
    ext.steer({STEER: 'left'});
    t.equal(last().angle, -30);
    next();
    ext.steer({STEER: 'right'});
    t.equal(last().angle, 30);
    next();
    ext.steer({STEER: 'straight'});
    t.equal(last().angle, 0);
    next();
    ext.setSteering({ANGLE: 99});
    t.equal(last().angle, 45);
    next();
    ext.setSteering({ANGLE: -12.5});
    t.same(last(), {t: 'cmd', id: last().id, op: 'steer', angle: -12.5});
    next();
    ext.setObstacleStop({ONOFF: 'off'});
    t.same(last(), {t: 'cmd', id: last().id, op: 'config', obstacle_stop: false});
    next();
    ext.setObstacleStop({ONOFF: 'on'});
    t.equal(last().obstacle_stop, true);
    next();
    ext.setStopDistance({CM: 5});
    t.equal(last().stop_cm, 10);
    next();
    ext.setStopDistance({CM: 35});
    t.equal(last().stop_cm, 35);
    next();
    ext.setSpeedLimit({PERCENT: 0});
    t.equal(last().speed_limit, 1);
    next();
    ext.setSpeedLimit({PERCENT: 60});
    t.same(last(), {t: 'cmd', id: last().id, op: 'config', speed_limit: 60});
    next();
    ext.resetPosition();
    t.equal(last().op, 'reset_pose');
    ext.emergencyStop();
    t.equal(last().op, 'estop');
    t.equal(ctx.fetches.length, 1, 'REST estop too');
    t.end();
});

test('driveFor returns the move promise; no-op for SECS <= 0; numbers clamped', async t => {
    const ctx = setup();
    const {ext, ws} = ctx;
    const p = ext.driveFor({DIR: 'backward', SPEED: '40', SECS: '1.5'});
    t.ok(p && typeof p.then === 'function');
    t.same(ws.cmds('move')[0], {t: 'cmd', id: 1, op: 'move', dir: 'backward', speed: 40, ms: 1500});
    ws.serverSend({t: 'done', id: 1, result: 'completed'});
    t.equal(await p, 'completed');

    t.equal(ext.driveFor({DIR: 'forward', SPEED: 50, SECS: 0}), undefined);
    t.equal(ext.driveFor({DIR: 'forward', SPEED: 50, SECS: -3}), undefined);
    t.equal(ext.driveFor({DIR: 'forward', SPEED: 50, SECS: 'x'}), undefined);
    t.equal(ws.cmds('move').length, 1);

    const big = ext.driveFor({DIR: 'forward', SPEED: 900, SECS: Infinity});
    t.equal(ws.cmds('move')[1].ms, 30000);
    t.equal(ws.cmds('move')[1].speed, 100);
    ws.serverSend({t: 'err', id: ws.cmds('move')[1].id, code: 'estop', msg: ''});
    t.equal(await big, 'estop');
});

test('driveFor resolves "disconnected" when the link drops', async t => {
    const ctx = setup();
    const p = ctx.ext.driveFor({DIR: 'forward', SPEED: 50, SECS: 5});
    ctx.ws.serverClose(1006);
    t.equal(await p, 'disconnected');
});

test('reporters have safe defaults when disconnected', t => {
    const ctx = setup(null);
    const {ext} = ctx;
    t.equal(ext.distance(), 999);
    t.equal(ext.obstacleCloser({CM: 20}), false);
    t.equal(ext.lineValue({SENSOR: '1'}), 0);
    t.equal(ext.lineSees({SENSOR: '3'}), false);
    t.equal(ext.linePosition(), 0);
    t.equal(ext.lightValue({LIGHT: 'middle'}), 0);
    t.equal(ext.brightestDirection(), 'none');
    t.equal(ext.isConnected(), false);
    t.equal(ext.carMode(), 'offline');
    t.same([ext.carX(), ext.carY(), ext.carDirection()], [0, 0, 90]);
    t.end();
});

test('reporters read current telemetry; telemetry cleared on disconnect', t => {
    const ctx = setup();
    const {ext, ws} = ctx;
    t.equal(ext.distance(), 999, 'no tel yet: far');
    ws.serverSend(tel());
    t.equal(ext.distance(), 42.3);
    t.equal(ext.obstacleCloser({CM: 50}), true);
    t.equal(ext.obstacleCloser({CM: 42}), false);
    t.equal(ext.lineValue({SENSOR: '2'}), 200);
    t.equal(ext.lineValue({SENSOR: 5}), 500);
    t.equal(ext.lineValue({SENSOR: 9}), 0);
    t.equal(ext.lineSees({SENSOR: '1'}), true);
    t.equal(ext.lineSees({SENSOR: '3'}), false);
    t.equal(ext.linePosition(), -2);
    t.equal(ext.lightValue({LIGHT: 'left'}), 41);
    t.equal(ext.lightValue({LIGHT: 'middle'}), 63);
    t.equal(ext.lightValue({LIGHT: 'right'}), 30);
    t.equal(ext.brightestDirection(), 'middle');
    t.equal(ext.carMode(), 'car');
    t.same([ext.carX(), ext.carY(), ext.carDirection()], [12.4, 31, 85.5]);

    ws.serverSend(tel({dist: {cm: null, state: 'far'}, line: {ok: true, raw: null, on: null, pos: null}}));
    t.equal(ext.distance(), 999);
    t.equal(ext.obstacleCloser({CM: 100}), false);
    t.equal(ext.linePosition(), 0);
    t.equal(ext.lineValue({SENSOR: 1}), 0);

    ws.serverSend(tel({dist: {cm: null, state: 'fault'}}));
    t.equal(ext.distance(), 0, 'fault -> 0');
    t.equal(ext.obstacleCloser({CM: 1}), true, 'fault is fail-safe true');

    ws.serverClose(1006);
    t.equal(ext.distance(), 999);
    t.equal(ext.obstacleCloser({CM: 1}), false, 'disconnected -> false');
    t.equal(ext.carMode(), 'offline');
    t.end();
});

test('PROJECT_START arms and sends resume; PROJECT_STOP_ALL disarms and sends stop (not resume)', t => {
    const ctx = setup();
    t.equal(ctx.ext.armed, false);
    ARM(ctx);
    t.equal(ctx.ext.armed, true);
    t.same(ctx.ws.cmds().map(c => c.op), ['resume']);
    ctx.runtime.emit('PROJECT_STOP_ALL');
    t.equal(ctx.ext.armed, false);
    t.same(ctx.ws.cmds().map(c => c.op), ['resume', 'stop']);
    t.end();
});

test('armed gate: edge hats stay false until armed and connected', t => {
    const ctx = setup();
    ctx.ws.serverSend(tel({dist: {cm: 5, state: 'ok'}}));
    t.equal(ctx.ext.whenObstacle({CM: 20}), false, 'not armed (project just loaded)');
    t.equal(ctx.ext.whenLineSees({SENSOR: 1}), false);
    ARM(ctx);
    t.equal(ctx.ext.whenObstacle({CM: 20}), true);
    t.equal(ctx.ext.whenObstacle({CM: 3}), false);
    t.equal(ctx.ext.whenLineSees({SENSOR: 1}), true);
    t.equal(ctx.ext.whenLineSees({SENSOR: 2}), false);
    ctx.runtime.emit('PROJECT_STOP_ALL');
    t.equal(ctx.ext.whenObstacle({CM: 20}), false, 'stop sign disarms');
    t.equal(ctx.ext.whenLineSees({SENSOR: 1}), false);
    ARM(ctx);
    ctx.ws.serverClose(1006);
    t.equal(ctx.ext.whenObstacle({CM: 20}), false, 'not connected');
    t.end();
});

test('whenCarEvent returns true; events start hats with a fresh object, only when armed', t => {
    const ctx = setup();
    t.equal(ctx.ext.whenCarEvent({EVENT: 'obstacle'}), true);
    ctx.ws.serverSend({t: 'event', kind: 'obstacle_stop', cm: 10});
    t.same(ctx.runtime.hats, [], 'not armed: nothing');
    ARM(ctx);
    ctx.ws.serverSend({t: 'event', kind: 'obstacle_stop', cm: 10});
    ctx.ws.serverSend({t: 'event', kind: 'obstacle_stop', cm: 9});
    t.same(ctx.runtime.hats.map(h => [h.opcode, h.fields]),
        [['picar_whenCarEvent', {EVENT: 'obstacle'}], ['picar_whenCarEvent', {EVENT: 'obstacle'}]]);
    t.not(ctx.runtime.hats[0].fields, ctx.runtime.hats[1].fields, 'fresh object per call');
    ctx.ws.serverSend({t: 'event', kind: 'sensor_fault', reason: 'x'});
    ctx.ws.serverSend({t: 'event', kind: 'watchdog_stop'});
    t.equal(ctx.runtime.hats.length, 2, 'only obstacle_stop maps to obstacle');
    t.equal(ctx.runtime.stopAllCalls, 0);
    t.end();
});

test('link open -> closed starts the "disconnected" hat when armed', t => {
    const unarmed = setup();
    unarmed.ws.serverClose(1006);
    t.same(unarmed.runtime.hats, []);

    const ctx = setup();
    ARM(ctx);
    ctx.ws.serverClose(1006);
    t.same(ctx.runtime.hats.map(h => h.fields), [{EVENT: 'disconnected'}]);
    // the reconnect attempts do not retrigger it
    ctx.clock.advance(600);
    FakeWebSocket.last.serverClose(1006);
    t.equal(ctx.runtime.hats.length, 1);
    t.end();
});

test('estop / hw_fault event: runtime.stopAll when this session controls; estop hat when armed', t => {
    const ctx = setup();
    ARM(ctx);
    ctx.ws.serverSend({t: 'event', kind: 'estop', by: 'other'});
    t.equal(ctx.runtime.stopAllCalls, 1);
    t.same(ctx.runtime.hats.map(h => h.fields), [{EVENT: 'estop'}]);
    t.equal(ctx.ext.armed, false, 'stopAll disarmed');
    t.ok(ctx.ws.cmds('stop').length >= 1, 'stop sent via PROJECT_STOP_ALL');
    ctx.ws.serverSend({t: 'event', kind: 'hw_fault', msg: 'i2c'});
    t.equal(ctx.runtime.stopAllCalls, 2);
    t.equal(ctx.runtime.hats.length, 1, 'hw_fault has no hat');
    t.end();
});

test('estop event does not stop the project of a viewer of the shared car, but does in sim', t => {
    const viewer = setup({role: 'viewer'});
    viewer.ws.serverSend({t: 'event', kind: 'estop', by: 'x'});
    t.equal(viewer.runtime.stopAllCalls, 0);

    const sim = setup({role: 'viewer', mode: 'sim'});
    sim.ws.serverSend({t: 'event', kind: 'estop', by: 'x'});
    t.equal(sim.runtime.stopAllCalls, 1, 'own sim');
    t.end();
});

test('the extension never makes the link emit "error"', t => {
    const ctx = setup();
    ctx.link.on('error', () => t.fail('error emitted'));
    ctx.ws.serverSend('garbage');
    ctx.ws.serverClose(1006);
    t.pass('survived');
    t.end();
});

test('registered as builtin extension "picar"', t => {
    const fs = require('fs');
    const path = require('path');
    const src = fs.readFileSync(path.join(__dirname, '../../src/extension-support/extension-manager.js'), 'utf8');
    t.match(src, /picar: \(\) => require\('\.\.\/extensions\/scratch3_picar'\)/);
    t.end();
});
