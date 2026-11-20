const EventEmitter = require('events');
const P = require('./protocol');

const WS_OPEN = 1;

const defaultClock = () => ({
    now: () => Date.now(),
    setTimeout: (fn, ms) => globalThis.setTimeout(fn, ms),
    clearTimeout: h => globalThis.clearTimeout(h),
    setInterval: (fn, ms) => globalThis.setInterval(fn, ms),
    clearInterval: h => globalThis.clearInterval(h)
});

/**
 * Read a browser storage object without ever throwing (access itself can throw when storage is blocked).
 * @param {string} name - 'sessionStorage' or 'localStorage'
 * @returns {?Storage} the storage or null
 */
const globalStorage = name => {
    try {
        return globalThis[name] || null;
    } catch {
        return null;
    }
};

const shallowEqual = (a, b) => {
    if (!a || !b) return a === b;
    const ka = Object.keys(a);
    if (ka.length !== Object.keys(b).length) return false;
    return ka.every(k => a[k] === b[k]);
};

/**
 * Transport between the Pi Car extension / Car Monitor and the car server (docs/protocol.md).
 * Never emits the special EventEmitter 'error' event.
 *
 * Events: 'telemetry' (tel), 'event' (evt), 'world' (world), 'state' (state), 'role' (msg),
 * 'protocol_error' ({reason, data}).
 */
class CarLink extends EventEmitter {
    /**
     * @param {object} [options] - options (everything is injectable for tests)
     * @param {string} [options.url] - WebSocket URL (default: ?picar_ws= override or same-origin /ws)
     * @param {Function} [options.WebSocketImpl] - WebSocket constructor
     * @param {object} [options.clock] - {now, setTimeout, clearTimeout, setInterval, clearInterval}
     * @param {object} [options.doc] - document-like (visibilityState, addEventListener)
     * @param {object} [options.win] - window-like (addEventListener for pageshow/pagehide/online)
     * @param {object} [options.sessionStore] - sessionStorage-like (alias: options.storage)
     * @param {object} [options.localStore] - localStorage-like
     * @param {Function} [options.fetchImpl] - fetch for POST /api/estop
     * @param {Function} [options.random] - Math.random replacement
     * @param {string} [options.buildSha] - editor build sha; a different welcome.server.sha means 'reload'
     */
    constructor (options = {}) {
        super();
        const o = options;
        this._url = o.url || P.defaultUrl(globalThis.location);
        this._WebSocket = o.WebSocketImpl || globalThis.WebSocket;
        this._clock = Object.assign(defaultClock(), o.clock || {});
        this._doc = 'doc' in o ? o.doc : globalThis.document;
        this._win = 'win' in o ? o.win : globalThis.window;
        this._session = o.sessionStore || o.storage || globalStorage('sessionStorage');
        this._local = o.localStore || globalStorage('localStorage');
        this._fetch = 'fetchImpl' in o ? o.fetchImpl : globalThis.fetch;
        this._random = o.random || Math.random;
        this._buildSha = o.buildSha || null;

        this._memMode = null;
        this.clientId = this._loadClientId();

        this._ws = null;
        this._started = false;
        this._stopped = false;
        this._noReconnect = false;
        this._attempt = 0;
        this._reconnectTimer = null;
        this._hbTimer = null;
        this._watchdog = null;
        this._lastFrameAt = 0;
        this._nextId = 0;
        this._tel = null;
        this._estopQueued = false;
        this._autoSimDone = false;
        this._worldPref = null;
        this._switchFrom = null;
        this._moves = new Map();
        this._pending = {};
        this._pendingTimers = {};
        this._lastSent = {};
        this._lastConfig = null;
        this._subscribers = new Set();
        this._boundVisibility = () => this._onVisibility();
        this._boundPageShow = () => this._reconnectNow();
        this._boundPageHide = () => this._pause();
        this._boundOnline = () => this._reconnectNow();

        this._state = Object.freeze({
            connection: 'closed',
            role: 'viewer',
            mode: this._loadMode(),
            carAvailable: true,
            hw: null,
            config: null,
            safety: null,
            world: null,
            worlds: [],
            geometry: null,
            server: null,
            lastError: null
        });
    }

    // ---- public API ----------------------------------------------------------------------------

    /** Connect and keep reconnecting (except after close 4400/4409). */
    start () {
        if (this._started && !this._stopped) return;
        this._started = true;
        this._stopped = false;
        this._noReconnect = false;
        this._addListeners();
        this._connect();
    }

    /** Close the socket and do not reconnect. */
    stop () {
        this._stopped = true;
        this._removeListeners();
        this._clearReconnect();
        const ws = this._ws;
        if (ws && ws.readyState === WS_OPEN && this._state.connection === 'open') {
            this._transmit('stop', {});
        }
        this._ws = null;
        this._teardown();
        if (ws) {
            try {
                ws.close(1000);
            } catch { /* already closed */ }
        }
        this._setState({connection: 'closed'});
    }

    /**
     * Switch between the shared car and a private simulator; persisted in localStorage picar.mode.
     * @param {string} mode - 'car' or 'sim'
     * @param {string} [world] - sim world id
     */
    setMode (mode, world) {
        if (mode !== 'car' && mode !== 'sim') return;
        this._saveMode(mode);
        this._worldPref = world || null;
        const conn = this._state.connection;
        if (this._ws && this._ws.readyState === WS_OPEN && (conn === 'open' || conn === 'switching')) {
            if (conn === 'open' && this._state.mode === mode && !world) return;
            if (conn === 'open') this._switchFrom = this._state.mode;
            this._clearPending();
            this._lastSent = {};
            this._lastConfig = null;
            this._tel = null;
            this._failMoves('disconnected');
            this._setState({connection: 'switching', mode, world: null});
            const msg = {t: 'mode', mode};
            if (world) msg.world = world;
            this._rawSend(msg);
        } else {
            this._setState({mode});
        }
    }

    /**
     * Fire-and-forget command through the coalescer.
     * @param {string} op - protocol op
     * @param {object} [fields] - op fields
     * @returns {?number} the cmd id when sent now, 0 when queued for the next 50 ms flush, null when dropped
     */
    send (op, fields = {}) {
        const ws = this._ws;
        const sockOpen = !!ws && ws.readyState === WS_OPEN;
        if (!sockOpen) return null;
        const conn = this._state.connection;
        let allowed = conn === 'open' || (conn === 'switching' && P.SWITCHING_OPS.includes(op));
        if (op === 'estop') allowed = true;
        if (!allowed) return null;
        if (this._backpressured() && !P.BACKPRESSURE_OPS.includes(op)) return null;
        if (P.COALESCED_OPS.includes(op)) return this._coalesce(op, fields);
        if (P.BYPASS_OPS.includes(op)) this._bypass();
        return this._transmit(op, fields);
    }

    /**
     * Timed move executed on the server. Always resolves.
     * @param {string} dir - 'forward' | 'backward'
     * @param {number} speed - 0..100
     * @param {number} ms - 1..30000
     * @returns {Promise<string>} done.result | err.code | 'disconnected' | 'timeout'
     */
    move (dir, speed, ms) {
        return new Promise(resolve => {
            const ws = this._ws;
            if (!ws || ws.readyState !== WS_OPEN || this._state.connection !== 'open') {
                resolve('disconnected');
                return;
            }
            if (this._backpressured()) {
                resolve('rate_limited');
                return;
            }
            const fields = {
                dir: dir === 'backward' ? 'backward' : 'forward',
                speed: Math.round(P.clamp(speed, 0, 100)),
                ms: Math.round(P.clamp(ms, 1, P.MOVE_MS_MAX, 1))
            };
            this._bypass();
            const id = this._transmit('move', fields);
            if (id === null) {
                resolve('disconnected');
                return;
            }
            const timer = this._clock.setTimeout(() => this._finishMove(id, 'timeout'),
                fields.ms + P.MOVE_TIMEOUT_SLACK_MS);
            this._moves.set(id, {resolve, timer});
        });
    }

    /** Emergency stop: WebSocket estop + POST /api/estop (keepalive); queued if the socket is not open. */
    estop () {
        this._bypass();
        this._postEstop();
        const ws = this._ws;
        if (ws && ws.readyState === WS_OPEN) {
            if (this._transmit('estop', {}) === null) this._estopQueued = true;
        } else {
            this._estopQueued = true;
        }
    }

    /**
     * Car arbitration.
     * @param {string} action - 'take' | 'release'
     * @param {boolean} [force] - take over from the current controller
     * @returns {boolean} whether the frame was sent
     */
    control (action, force) {
        if (this._state.connection !== 'open') return false;
        const msg = {t: 'control', action};
        if (force) msg.force = true;
        return this._rawSend(msg);
    }

    /** @returns {object} immutable snapshot of the coarse state */
    getState () {
        return this._state;
    }

    /**
     * Subscribe to coarse state changes (not every tel).
     * @param {Function} fn - called with the new state
     * @returns {Function} unsubscribe
     */
    subscribe (fn) {
        this._subscribers.add(fn);
        return () => this._subscribers.delete(fn);
    }

    /** @returns {?object} the last tel frame, or null (cleared on disconnect and on every mode switch) */
    getTelemetry () {
        return this._tel;
    }

    // ---- state ---------------------------------------------------------------------------------

    _setState (patch) {
        let changed = false;
        for (const k of Object.keys(patch)) {
            if (JSON.stringify(patch[k]) !== JSON.stringify(this._state[k])) {
                changed = true;
                break;
            }
        }
        if (!changed) return;
        this._state = Object.freeze(Object.assign({}, this._state, patch));
        this.emit('state', this._state);
        for (const fn of Array.from(this._subscribers)) {
            try {
                fn(this._state);
            } catch { /* a subscriber must not break the link */ }
        }
    }

    // ---- storage -------------------------------------------------------------------------------

    _loadClientId () {
        try {
            const v = this._session && this._session.getItem(P.SESSION_KEY);
            if (typeof v === 'string' && v.length >= 8 && v.length <= 64) return v;
        } catch { /* fall through to a fresh id */ }
        let id = '';
        const alphabet = 'abcdefghijklmnopqrstuvwxyz0123456789';
        for (let i = 0; i < 24; i++) id += alphabet[Math.floor(this._random() * alphabet.length) % alphabet.length];
        try {
            if (this._session) this._session.setItem(P.SESSION_KEY, id);
        } catch { /* in-memory only */ }
        return id;
    }

    _loadMode () {
        try {
            const v = this._local && this._local.getItem(P.MODE_KEY);
            if (v === 'car' || v === 'sim') return v;
        } catch { /* default */ }
        return this._memMode || 'car';
    }

    _saveMode (mode) {
        this._memMode = mode;
        try {
            if (this._local) this._local.setItem(P.MODE_KEY, mode);
        } catch { /* in-memory only */ }
    }

    // ---- sending -------------------------------------------------------------------------------

    _backpressured () {
        return !!this._ws && this._ws.bufferedAmount > P.MAX_BUFFERED_BYTES;
    }

    _rawSend (obj) {
        const ws = this._ws;
        if (!ws || ws.readyState !== WS_OPEN) return false;
        try {
            ws.send(JSON.stringify(obj));
            return true;
        } catch {
            return false;
        }
    }

    _transmit (op, fields) {
        const id = this._nextId + 1;
        if (!this._rawSend(Object.assign({t: 'cmd', id, op}, fields))) return null;
        this._nextId = id;
        return id;
    }

    /** Bypass ops clear pending motion ops (and forget what was last sent, so a repeat is real). */
    _bypass () {
        this._clearPending();
        this._bypassMemory();
    }

    _bypassMemory () {
        delete this._lastSent.drive;
        delete this._lastSent.set_speed;
    }

    _clearPending () {
        for (const op of Object.keys(this._pendingTimers)) this._clock.clearTimeout(this._pendingTimers[op]);
        this._pendingTimers = {};
        this._pending = {};
    }

    _isDuplicate (op, fields, now) {
        if (op === 'config') {
            const last = this._lastConfig;
            return !!last && Object.keys(fields).every(k => last[k] === fields[k]);
        }
        const last = this._lastSent[op];
        if (!last || !shallowEqual(last.fields, fields)) return false;
        // The server's drive timeout (guard off) needs a fresh motion command now and then.
        return op !== 'drive' || now - last.at < P.REFRESH_MS;
    }

    _coalesce (op, fields) {
        const now = this._clock.now();
        const merged = op === 'config' ? Object.assign({}, this._pending.config, fields) : fields;
        if (this._pending[op]) {
            this._pending[op] = merged;
            return 0;
        }
        if (this._isDuplicate(op, merged, now)) return null;
        const last = this._lastSent[op];
        if (!last || now - last.at >= P.COALESCE_MS) return this._flush(op, merged, now);
        this._pending[op] = merged;
        this._pendingTimers[op] = this._clock.setTimeout(() => this._flushPending(op),
            P.COALESCE_MS - (now - last.at));
        return 0;
    }

    _flushPending (op) {
        const fields = this._pending[op];
        delete this._pending[op];
        delete this._pendingTimers[op];
        if (!fields || this._state.connection !== 'open' || this._backpressured()) return;
        const now = this._clock.now();
        const last = this._lastSent[op];
        const same = op === 'config' ? this._isDuplicate(op, fields, now) : last && shallowEqual(last.fields, fields);
        if (same) return;
        this._flush(op, fields, now);
    }

    _flush (op, fields, now) {
        const id = this._transmit(op, fields);
        if (id === null) return null;
        this._lastSent[op] = {fields, at: now};
        if (op === 'config') this._lastConfig = Object.assign({}, this._lastConfig, fields);
        return id;
    }

    _postEstop () {
        if (typeof this._fetch !== 'function') return;
        try {
            const p = this._fetch(P.estopUrl(this._url), {method: 'POST', keepalive: true});
            if (p && typeof p.catch === 'function') p.catch(() => {});
        } catch { /* best effort */ }
    }

    _pause () {
        if (this._ws) this.send('pause');
    }

    // ---- moves ---------------------------------------------------------------------------------

    _finishMove (id, result) {
        const m = this._moves.get(id);
        if (!m) return;
        this._moves.delete(id);
        this._clock.clearTimeout(m.timer);
        m.resolve(result);
    }

    _failMoves (result) {
        for (const id of Array.from(this._moves.keys())) this._finishMove(id, result);
    }

    // ---- connection ----------------------------------------------------------------------------

    _connect () {
        if (this._stopped || this._noReconnect) return;
        this._clearReconnect();
        this._setState({connection: 'connecting'});
        let ws;
        try {
            ws = new this._WebSocket(this._url);
        } catch (e) {
            this._setState({connection: 'closed', lastError: {code: 'connect_failed', msg: String(e && e.message)}});
            this._scheduleReconnect();
            return;
        }
        this._ws = ws;
        this._nextId = 0;
        this._lastFrameAt = this._clock.now();
        ws.onopen = () => {
            if (ws !== this._ws) return;
            this._lastFrameAt = this._clock.now();
            const hello = {
                t: 'hello', v: P.PROTOCOL_VERSION, client_id: this.clientId, want: 'control', mode: this._state.mode
            };
            if (this._state.mode === 'sim' && this._worldPref) hello.world = this._worldPref;
            this._rawSend(hello);
        };
        ws.onmessage = ev => {
            if (ws === this._ws) this._onMessage(ev && ev.data);
        };
        ws.onclose = ev => this._onClose(ws, ev && ev.code);
        ws.onerror = () => {};
        this._watchdog = this._clock.setInterval(() => this._checkLiveness(), P.WATCHDOG_TICK_MS);
    }

    _onClose (ws, code) {
        if (ws !== this._ws) return;
        this._ws = null;
        this._teardown();
        if (this._stopped) {
            this._setState({connection: 'closed'});
            return;
        }
        if (P.NO_RECONNECT_CODES.includes(code)) {
            this._noReconnect = true;
            this._setState({connection: 'reload', lastError: {code: `close_${code}`, msg: 'reload the page'}});
            return;
        }
        this._setState({connection: 'closed'});
        this._scheduleReconnect();
    }

    _teardown () {
        if (this._hbTimer !== null) this._clock.clearInterval(this._hbTimer);
        if (this._watchdog !== null) this._clock.clearInterval(this._watchdog);
        this._hbTimer = null;
        this._watchdog = null;
        this._clearPending();
        this._lastSent = {};
        this._lastConfig = null;
        this._tel = null;
        this._switchFrom = null;
        this._failMoves('disconnected');
    }

    _scheduleReconnect () {
        if (this._stopped || this._noReconnect) return;
        this._clearReconnect();
        const base = P.BACKOFF_MS[Math.min(this._attempt, P.BACKOFF_MS.length - 1)];
        this._attempt++;
        this._reconnectTimer = this._clock.setTimeout(() => {
            this._reconnectTimer = null;
            this._connect();
        }, base + (this._random() * P.JITTER_MS));
    }

    _clearReconnect () {
        if (this._reconnectTimer !== null) this._clock.clearTimeout(this._reconnectTimer);
        this._reconnectTimer = null;
    }

    _reconnectNow () {
        if (this._stopped || this._noReconnect || !this._started) return;
        if (this._ws || this._state.connection === 'reload') return;
        this._clearReconnect();
        this._connect();
    }

    _dropSocket () {
        const ws = this._ws;
        this._ws = null;
        this._teardown();
        if (ws) {
            try {
                ws.close();
            } catch { /* already closed */ }
        }
    }

    _checkLiveness () {
        const conn = this._state.connection;
        const limit = conn === 'connecting' ? P.CONNECT_STALE_MS : P.STALE_FRAME_MS;
        if (!this._ws || this._clock.now() - this._lastFrameAt <= limit) return;
        this._dropSocket();
        this._setState({connection: 'closed', lastError: {code: 'stale', msg: 'no frame from the car'}});
        this._scheduleReconnect();
    }

    _fatalReload (code, msg) {
        this._noReconnect = true;
        this._clearReconnect();
        this._dropSocket();
        this._setState({connection: 'reload', lastError: {code, msg}});
    }

    _onVisibility () {
        const d = this._doc;
        if (!d) return;
        if (d.visibilityState === 'hidden' || d.hidden === true) this._pause();
        else this._reconnectNow();
    }

    _addListeners () {
        if (this._doc && this._doc.addEventListener) {
            this._doc.addEventListener('visibilitychange', this._boundVisibility);
        }
        if (this._win && this._win.addEventListener) {
            this._win.addEventListener('pageshow', this._boundPageShow);
            this._win.addEventListener('pagehide', this._boundPageHide);
            this._win.addEventListener('online', this._boundOnline);
        }
    }

    _removeListeners () {
        if (this._doc && this._doc.removeEventListener) {
            this._doc.removeEventListener('visibilitychange', this._boundVisibility);
        }
        if (this._win && this._win.removeEventListener) {
            this._win.removeEventListener('pageshow', this._boundPageShow);
            this._win.removeEventListener('pagehide', this._boundPageHide);
            this._win.removeEventListener('online', this._boundOnline);
        }
    }

    // ---- incoming frames -----------------------------------------------------------------------

    _protocolError (reason, data) {
        this.emit('protocol_error', {reason, data});
    }

    _onMessage (data) {
        let msg;
        if (typeof data !== 'string') {
            this._protocolError('not_text', data);
            return;
        }
        try {
            msg = JSON.parse(data);
        } catch {
            this._protocolError('bad_json', data);
            return;
        }
        if (!msg || typeof msg !== 'object' || typeof msg.t !== 'string') {
            this._protocolError('bad_frame', data);
            return;
        }
        this._lastFrameAt = this._clock.now();
        switch (msg.t) {
        case 'welcome': this._onWelcome(msg); break;
        case 'tel': this._onTel(msg); break;
        case 'ack': break;
        case 'done': this._finishMove(msg.id, String(msg.result)); break;
        case 'err': this._onErr(msg); break;
        case 'event': this._onEvent(msg); break;
        case 'role': this._onRole(msg); break;
        case 'world':
            this._setState({world: msg});
            this.emit('world', msg);
            break;
        default: this._protocolError('unknown_type', msg);
        }
    }

    _onWelcome (msg) {
        if ('v' in msg && msg.v !== P.PROTOCOL_VERSION) {
            this._fatalReload('protocol_mismatch', 'protocol version mismatch');
            return;
        }
        const sha = msg.server && msg.server.sha;
        if (this._buildSha && sha && sha !== this._buildSha) {
            this._fatalReload('sha_mismatch', 'editor and car server versions differ');
            return;
        }
        this._attempt = 0;
        this._tel = null;
        this._switchFrom = null;
        this._clearPending();
        this._lastSent = {};
        this._lastConfig = null;
        this._setState({
            connection: 'open',
            role: msg.role === 'controller' ? 'controller' : 'viewer',
            mode: msg.mode === 'sim' ? 'sim' : 'car',
            carAvailable: msg.car_available !== false,
            hw: msg.hw || null,
            config: msg.config || null,
            safety: msg.safety || null,
            world: null,
            worlds: Array.isArray(msg.worlds) ? msg.worlds : [],
            geometry: msg.geometry || null,
            server: msg.server || null,
            lastError: null
        });
        if (this._hbTimer !== null) this._clock.clearInterval(this._hbTimer);
        const hb = Number(msg.hb_ms) > 0 ? Number(msg.hb_ms) : 200;
        this._hbTimer = this._clock.setInterval(() => this._rawSend({t: 'hb'}), hb);
        if (this._estopQueued) {
            this._estopQueued = false;
            this._transmit('estop', {});
        }
        if (!this._autoSimDone && msg.car_available === false && msg.mode === 'car') {
            this._autoSimDone = true;
            this.setMode('sim');
        }
    }

    _onTel (msg) {
        if (this._state.connection !== 'open') return;
        this._tel = msg;
        const patch = {};
        const s = msg.safety;
        if (s && typeof s === 'object') {
            patch.safety = {
                estop: s.estop, hold: s.hold, calibrated: s.calibrated, blocked: s.blocked, hw_fault: s.hw_fault
            };
            patch.config = {obstacle_stop: s.obstacle_stop, stop_cm: s.stop_cm, speed_limit: s.speed_limit};
        }
        if (msg.ctl && msg.ctl.role) patch.role = msg.ctl.role === 'controller' ? 'controller' : 'viewer';
        this._setState(patch);
        this.emit('telemetry', msg);
    }

    _onErr (msg) {
        this._bypassMemory();
        const err = {code: msg.code, msg: msg.msg, id: msg.id};
        if (typeof msg.id === 'number' && this._moves.has(msg.id)) {
            this._finishMove(msg.id, String(msg.code));
        } else if (this._state.connection === 'switching' && this._switchFrom) {
            // The server refused the mode switch (e.g. sim_busy): stay where we were.
            const back = this._switchFrom;
            this._switchFrom = null;
            this._saveMode(back);
            this._setState({connection: 'open', mode: back, lastError: err});
            return;
        }
        this._setState({lastError: err});
    }

    _onEvent (msg) {
        this._bypassMemory();
        const safety = this._state.safety || {};
        switch (msg.kind) {
        case 'estop': this._setState({safety: Object.assign({}, safety, {estop: true})}); break;
        case 'estop_reset': this._setState({safety: Object.assign({}, safety, {estop: false})}); break;
        case 'hold': this._setState({safety: Object.assign({}, safety, {hold: true})}); break;
        case 'resumed': this._setState({safety: Object.assign({}, safety, {hold: false})}); break;
        case 'hw_fault': this._setState({safety: Object.assign({}, safety, {hw_fault: msg.msg || true})}); break;
        default:
        }
        this.emit('event', msg);
    }

    _onRole (msg) {
        this._lastSent = {};
        this._lastConfig = null;
        this._setState({role: msg.role === 'controller' ? 'controller' : 'viewer'});
        this.emit('role', msg);
    }
}

CarLink.defaultUrl = P.defaultUrl;

module.exports = CarLink;
