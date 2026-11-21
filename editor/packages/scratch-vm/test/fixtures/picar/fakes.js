/**
 * Test doubles for the Pi Car extension and CarLink: fake WebSocket, fake clock, fake storage,
 * fake document/window and fake runtime.
 */
const EventEmitter = require('events');

class FakeClock {
    constructor () {
        this.t = 1000;
        this.timers = [];
        this.seq = 0;
        this.now = () => this.t;
        this.setTimeout = (fn, ms) => this._add(fn, ms, 0);
        this.clearTimeout = h => this._remove(h);
        this.setInterval = (fn, ms) => this._add(fn, ms, ms);
        this.clearInterval = h => this._remove(h);
    }

    _add (fn, ms, every) {
        const id = ++this.seq;
        this.timers.push({id, fn, due: this.t + ms, every});
        return id;
    }

    _remove (id) {
        this.timers = this.timers.filter(x => x.id !== id);
    }

    get pending () {
        return this.timers.length;
    }

    advance (ms) {
        const end = this.t + ms;
        for (;;) {
            const next = this.timers.filter(x => x.due <= end).sort((a, b) => a.due - b.due || a.id - b.id)[0];
            if (!next) break;
            this.t = Math.max(this.t, next.due);
            if (next.every) next.due += next.every;
            else this._remove(next.id);
            next.fn();
        }
        this.t = end;
    }
}

class FakeWebSocket {
    constructor (url) {
        this.url = url;
        this.readyState = 0;
        this.bufferedAmount = 0;
        this.sent = [];
        this.closedWith = null;
        FakeWebSocket.instances.push(this);
    }

    send (data) {
        if (this.readyState !== 1) throw new Error('not open');
        this.sent.push(JSON.parse(data));
    }

    close (code) {
        this.closedWith = code === undefined ? 1005 : code;
        this.readyState = 3;
    }

    // --- test drivers
    open () {
        this.readyState = 1;
        if (this.onopen) this.onopen({});
    }

    serverSend (obj) {
        if (this.onmessage) this.onmessage({data: typeof obj === 'string' ? obj : JSON.stringify(obj)});
    }

    serverClose (code) {
        this.readyState = 3;
        if (this.onclose) this.onclose({code});
    }

    ofType (t) {
        return this.sent.filter(m => m.t === t);
    }

    cmds (op) {
        return this.sent.filter(m => m.t === 'cmd' && (op === undefined || m.op === op));
    }

    static get last () {
        return FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
    }
}
FakeWebSocket.instances = [];

class FakeStore {
    constructor (initial = {}) {
        this.data = Object.assign({}, initial);
    }

    getItem (k) {
        return Object.prototype.hasOwnProperty.call(this.data, k) ? this.data[k] : null;
    }

    setItem (k, v) {
        this.data[k] = String(v);
    }
}

class ThrowingStore {
    getItem () {
        throw new Error('blocked');
    }

    setItem () {
        throw new Error('blocked');
    }
}

class FakeTarget {
    constructor () {
        this.handlers = {};
    }

    addEventListener (name, fn) {
        (this.handlers[name] = this.handlers[name] || []).push(fn);
    }

    removeEventListener (name, fn) {
        this.handlers[name] = (this.handlers[name] || []).filter(f => f !== fn);
    }

    fire (name) {
        (this.handlers[name] || []).slice().forEach(f => f({type: name}));
    }
}

const welcome = (overrides = {}) => Object.assign({
    t: 'welcome',
    v: 1,
    session: 's1',
    role: 'controller',
    mode: 'car',
    safety: {estop: false, hold: true},
    car_available: true,
    hw: {backend: 'real', capabilities: {ultrasonic: true, light: true, line: false}},
    limits: {speed_max: 100, steer_max: 45, move_ms_max: 30000},
    config: {obstacle_stop: true, stop_cm: 20, speed_limit: 100},
    calibrated: false,
    hb_ms: 200,
    wd_ms: 750,
    tel_hz: 20,
    worlds: [{id: 'arena', title: 'Arena'}],
    server: {version: '1', sha: 'abc', source_url: '/source/'}
}, overrides);

const tel = (overrides = {}) => Object.assign({
    t: 'tel',
    seq: 1,
    ts: 1.0,
    mode: 'car',
    drive: {dir: null, speed: 0, duty: [0, 0], moving: false},
    steer: 0,
    dist: {cm: 42.34, state: 'ok'},
    obstacle: false,
    line: {ok: true, raw: [100, 200, 300, 400, 500], on: [true, false, false, false, true], pos: -2},
    light: {ok: true, raw: [118, 96, 131], bright: [41, 63, 30], max: 'middle'},
    pose: {x: 12.44, y: 31.0, dir: 85.5, src: 'dr'},
    safety: {estop: false,
        hold: false,
        calibrated: false,
        obstacle_stop: true,
        stop_cm: 20,
        margin_cm: 24.5,
        speed_limit: 100,
        blocked: false,
        hw_fault: null},
    ctl: {role: 'controller', viewers: 0},
    move: null
}, overrides);

/**
 * Build a CarLink wired to fakes.
 * @param {Function} CarLink - the class
 * @param {object} [extra] - option overrides
 * @returns {object} {link, clock, doc, win, session, local, fetches}
 */
const makeLink = (CarLink, extra = {}) => {
    FakeWebSocket.instances = [];
    const clock = new FakeClock();
    const doc = new FakeTarget();
    doc.visibilityState = 'visible';
    const win = new FakeTarget();
    const session = new FakeStore();
    const local = new FakeStore();
    const fetches = [];
    const link = new CarLink(Object.assign({
        url: 'ws://car.local:8080/ws',
        WebSocketImpl: FakeWebSocket,
        clock,
        doc,
        win,
        sessionStore: session,
        localStore: local,
        fetchImpl: (url, opts) => {
            fetches.push({url, opts});
            return Promise.resolve({ok: true});
        },
        random: () => 0
    }, extra));
    return {link, clock, doc, win, session, local, fetches};
};

/**
 * Start a link and complete the handshake.
 * @param {object} ctx - from makeLink
 * @param {object} [welcomeOverrides] - welcome field overrides
 * @returns {FakeWebSocket} the socket
 */
const connect = (ctx, welcomeOverrides) => {
    ctx.link.start();
    const ws = FakeWebSocket.last;
    ws.open();
    ws.serverSend(welcome(welcomeOverrides));
    return ws;
};

class FakeRuntime extends EventEmitter {
    constructor () {
        super();
        this.peripheralExtensions = {};
        this.stopAllCalls = 0;
        this.hats = [];
    }

    registerPeripheralExtension (id, ext) {
        this.peripheralExtensions[id] = ext;
    }

    stopAll () {
        this.stopAllCalls++;
        this.emit('PROJECT_STOP_ALL');
    }

    startHats (opcode, fields) {
        this.hats.push({opcode, fields});
        return [];
    }
}

module.exports = {
    FakeClock, FakeWebSocket, FakeStore, ThrowingStore, FakeTarget, FakeRuntime, welcome, tel, makeLink, connect
};
