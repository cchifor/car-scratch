const EventEmitter = require('events');
const P = require('./protocol');

const WS_OPEN = 1;
// Coalesced ops that set state rather than motion: never dropped by a bypass op, sent before it.
const SETTING_OPS = Object.freeze(['steer', 'config']);

const defaultClock = () => ({
    // Monotonic: a wall-clock step backwards must not make the throttles drop later presses.
    now: globalThis.performance && globalThis.performance.now ? () => globalThis.performance.now() : () => Date.now(),
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
        this._memMirror = false;
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
        this._txByOp = {};
        this._otherTx = 0;
        this._lastEstopPostAt = null;
        this._estopPostTimer = null;
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
            stageMirror: this._loadStageMirror(),
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
        this._clearEstopPostTimer();
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
     * @param {object} [opts] - options
     * @param {boolean} [opts.persist] - false keeps picar.mode untouched (an automatic fallback, not a choice)
     */
    setMode (mode, world, opts) {
        if (mode !== 'car' && mode !== 'sim') return;
        if (!opts || opts.persist !== false) this._saveMode(mode);
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
        // A reset supersedes any E-STOP POST still waiting at the end of its throttle window.
        if (op === 'estop_reset') this._clearEstopPostTimer();
        const ws = this._ws;
        const sockOpen = !!ws && ws.readyState === WS_OPEN;
        if (!sockOpen) return null;
        const conn = this._state.connection;
        let allowed = conn === 'open' || (conn === 'switching' && P.SWITCHING_OPS.includes(op));
        if (op === 'estop') allowed = true;
        if (!allowed) return null;
        // A zero speed is always a stop: it takes the bypass path and is never de-duplicated away.
        if ((op === 'set_speed' || op === 'drive') && fields.speed === 0) {
            op = 'stop';
            fields = {};
        }
        if (this._backpressured() && !P.BACKPRESSURE_OPS.includes(op)) return null;
        if (P.COALESCED_OPS.includes(op)) return this._coalesce(op, fields);
        if (P.BYPASS_OPS.includes(op)) {
            const after = this._bypass(op);
            if (op !== 'move' && this._repeatsLastFrame(op)) {
                this._sendAfter(after);
                return null;
            }
            const id = this._transmit(op, fields);
            this._sendAfter(after);
            return id;
        }
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
            this._bypass('move');
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

    /**
     * Emergency stop: WebSocket estop + POST /api/estop (keepalive); queued if the socket is not open.
     * Emits a local {kind: 'estop', local: true} event at once.
     */
    estop () {
        this._bypass('estop');
        const now = this._clock.now();
        const since = this._lastEstopPostAt === null ? null : now - this._lastEstopPostAt;
        if (since === null || since < 0 || since >= P.ESTOP_POST_MS) {
            this._clearEstopPostTimer();
            this._lastEstopPostAt = now;
            this._postEstop();
        } else if (this._estopPostTimer === null) {
            // Throttled: one trailing POST at the end of the window, so the last press is never lost.
            this._estopPostTimer = this._clock.setTimeout(() => {
                this._estopPostTimer = null;
                this._lastEstopPostAt = this._clock.now();
                this._postEstop();
            }, P.ESTOP_POST_MS - since);
        }
        const ws = this._ws;
        if (ws && ws.readyState === WS_OPEN) {
            if (!this._repeatsLastFrame('estop') && this._transmit('estop', {}) === null) this._estopQueued = true;
        } else {
            this._estopQueued = true;
        }
        // The project must stop even when the server's estop event never comes back.
        this.emit('event', {kind: 'estop', local: true});
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

    /**
     * Whether the Car sprite on the stage follows the car pose; persisted in localStorage picar.stageMirror.
     * @param {boolean} on - mirror on or off
     */
    setStageMirror (on) {
        const v = Boolean(on);
        this._memMirror = v;
        try {
            if (this._local) this._local.setItem(P.STAGE_MIRROR_KEY, v ? '1' : '0');
        } catch { /* in-memory only */ }
        this._setState({stageMirror: v});
    }

    /** Erase the stage-mirror trail (the GUI's "Clear map"); the extension listens for 'clearTrail'. */
    clearTrail () {
        this.emit('clearTrail');
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

    _loadStageMirror () {
        try {
            const v = this._local && this._local.getItem(P.STAGE_MIRROR_KEY);
            if (v === '1') return true;
            if (v === '0') return false;
        } catch { /* default */ }
        return this._memMirror === true;
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
        if (!P.SAFETY_FAMILY_OPS.includes(op)) this._otherTx++;
        this._txByOp[op] = {at: this._clock.now(), other: this._otherTx};
        return id;
    }

    /**
     * Whether a field-less safety op repeats the last one sent, within COALESCE_MS, with no other kind of frame
     * sent since (stop, pause and estop belong together: an E-STOP press sends both estop and stop): a loop of
     * "stop motors" must not flood the socket.
     * @param {string} op - protocol op
     * @returns {boolean} true to drop it (never for unlatching ops: resume and estop_reset always go out)
     */
    _repeatsLastFrame (op) {
        if (!P.SAFETY_FAMILY_OPS.includes(op)) return false;
        const last = this._txByOp[op];
        if (!last || last.other !== this._otherTx) return false;
        const dt = this._clock.now() - last.at;
        return dt >= 0 && dt < P.COALESCE_MS;
    }

    /**
     * Before a bypass op: pending MOTION ops (drive, set_speed) are superseded by it and dropped, but pending
     * SETTINGS (steer, config) are sent now, in order, so "steer left" + "drive for 4 s" within 50 ms still
     * steers (on-car finding 2026-11-21: the figure eight drove straight). Also forgets what was last sent, so
     * a repeat is real.
     */
    _bypass (op) {
        this._dropPending('drive');
        this._dropPending('set_speed');
        let after = [];
        if (op === 'estop') {
            // E-STOP halts everything and goes out first: pending settings are dropped
        } else if (op === 'stop' || op === 'pause') {
            // a stop is never delayed behind a setting (a steer is an I2C write on the car): send them after it
            after = SETTING_OPS.filter(s => s in this._pending).map(s => [s, this._pending[s]]);
        } else {
            // move / resume / estop_reset: the setting must apply before the motion
            for (const s of SETTING_OPS) {
                if (s in this._pending) this._flushPending(s);
            }
        }
        this._clearPending();
        this._bypassMemory();
        return after;
    }

    /**
     * Sends settings taken from the coalescer by _bypass, after the stop they waited behind.
     * @param {Array} after - [op, fields] pairs
     */
    _sendAfter (after) {
        for (const [op, fields] of after) {
            const now = this._clock.now();
            const last = this._lastSent[op];
            const same = op === 'config' ?
                this._isDuplicate(op, fields, now) :
                last && shallowEqual(last.fields, fields);
            if (!same) this._flush(op, fields, now);
        }
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
        // A zero speed is never dropped (send() turns it into a stop before it gets here).
        if (fields.speed === 0) return false;
        // The server's drive timeout (guard off) needs a fresh motion command now and then.
        return op !== 'drive' || now - last.at < P.REFRESH_MS;
    }

    _dropPending (op) {
        if (op in this._pendingTimers) this._clock.clearTimeout(this._pendingTimers[op]);
        delete this._pendingTimers[op];
        delete this._pending[op];
    }

    _coalesce (op, fields) {
        const now = this._clock.now();
        // drive and set_speed both set the speed, so the last request wins whatever its op: a set_speed folds into
        // a pending drive (the drive carries the direction), and a drive replaces a pending set_speed.
        if (op === 'set_speed' && this._pending.drive) {
            this._pending.drive = Object.assign({}, this._pending.drive, {speed: fields.speed});
            return 0;
        }
        if (op === 'drive') this._dropPending('set_speed');
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
        // also called directly (by _bypass): cancel the scheduled flush, or it would fire later for a newer value
        if (op in this._pendingTimers) this._clock.clearTimeout(this._pendingTimers[op]);
        delete this._pending[op];
        delete this._pendingTimers[op];
        if (!fields || this._state.connection !== 'open' || this._backpressured()) return;
        const now = this._clock.now();
        const last = this._lastSent[op];
        const same = op === 'config' ? this._isDuplicate(op, fields, now) : last && shallowEqual(last.fields, fields);
        if (same && fields.speed !== 0) return;
        this._flush(op, fields, now);
    }

    _flush (op, fields, now) {
        const id = this._transmit(op, fields);
        if (id === null) return null;
        this._lastSent[op] = {fields, at: now};
        // drive and set_speed both set the speed: whichever went last owns what the car is doing.
        if (op === 'drive') delete this._lastSent.set_speed;
        if (op === 'set_speed') delete this._lastSent.drive;
        if (op === 'config') this._lastConfig = Object.assign({}, this._lastConfig, fields);
        return id;
    }

    _clearEstopPostTimer () {
        if (this._estopPostTimer !== null) this._clock.clearTimeout(this._estopPostTimer);
        this._estopPostTimer = null;
    }

    /** Any inbound safety change invalidates what the throttles remember: the next E-STOP press must go out. */
    _safetyChanged () {
        this._otherTx++;
        this._lastEstopPostAt = null;
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
        this._txByOp = {};
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
        // 'dev' (a bundle built outside the release pipeline, or the simulator) never matches: skip the check.
        const known = v => typeof v === 'string' && v !== '' && v !== 'dev';
        // The server reports a 12-char short sha: tolerate a longer or shorter form of the same commit (>= 7 chars).
        const sameSha = (a, b) => a === b || (a.length >= 7 && b.length >= 7 && (a.startsWith(b) || b.startsWith(a)));
        if (known(this._buildSha) && known(sha) && !sameSha(sha, this._buildSha)) {
            this._fatalReload('sha_mismatch', 'editor and car server versions differ');
            return;
        }
        this._attempt = 0;
        this._safetyChanged();
        this._tel = null;
        this._switchFrom = null;
        this._clearPending();
        this._lastSent = {};
        this._txByOp = {};
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
        const d = this._doc;
        if (d && (d.visibilityState === 'hidden' || d.hidden === true)) this._pause();
        if (!this._autoSimDone && msg.car_available === false && msg.mode === 'car') {
            this._autoSimDone = true;
            this.setMode('sim', null, {persist: false});
        }
    }

    _onTel (msg) {
        if (this._state.connection !== 'open') return;
        this._tel = msg;
        const patch = {};
        const s = msg.safety;
        if (s && typeof s === 'object') {
            const prev = this._state.safety || {};
            if (Boolean(prev.estop) !== Boolean(s.estop) || Boolean(prev.hold) !== Boolean(s.hold)) {
                this._safetyChanged();
            }
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
        } else if (this._state.connection === 'switching' && this._switchFrom && typeof msg.id !== 'number') {
            // An err without an id while switching: the server refused the mode switch (e.g. sim_busy).
            // An err that carries an id belongs to a command and leaves the switch pending.
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
        this._safetyChanged();
        const safety = this._state.safety || {};
        switch (msg.kind) {
        case 'estop': this._setState({safety: Object.assign({}, safety, {estop: true})}); break;
        case 'estop_reset':
            this._clearEstopPostTimer();
            this._setState({safety: Object.assign({}, safety, {estop: false})});
            break;
        case 'hold': this._setState({safety: Object.assign({}, safety, {hold: true})}); break;
        case 'resumed': this._setState({safety: Object.assign({}, safety, {hold: false})}); break;
        case 'hw_fault': this._setState({safety: Object.assign({}, safety, {hw_fault: msg.msg || true})}); break;
        default:
        }
        this.emit('event', msg);
    }

    _onRole (msg) {
        this._safetyChanged();
        this._lastSent = {};
        this._lastConfig = null;
        this._setState({role: msg.role === 'controller' ? 'controller' : 'viewer'});
        this.emit('role', msg);
    }
}

CarLink.defaultUrl = P.defaultUrl;

module.exports = CarLink;
