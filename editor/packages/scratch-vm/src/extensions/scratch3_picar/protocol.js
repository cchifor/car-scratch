/**
 * Constants and small helpers for the Car Scratch wire protocol (docs/protocol.md, docs/contracts.md section 5).
 */

const PROTOCOL_VERSION = 1;

/** Ops that bypass the coalescer and clear pending motion ops. */
const BYPASS_OPS = Object.freeze(['stop', 'estop', 'pause', 'resume', 'move', 'estop_reset']);

/** Ops that are de-duplicated and rate limited (per-op latest wins). */
const COALESCED_OPS = Object.freeze(['drive', 'steer', 'set_speed', 'config']);

/** The only ops sent while the socket is back-pressured. */
const BACKPRESSURE_OPS = Object.freeze(['stop', 'estop', 'pause', 'hb']);

/** Ops allowed while the connection is `switching` (no welcome yet, motion is dropped). */
const SWITCHING_OPS = Object.freeze(['stop', 'estop', 'pause']);

/** Close codes after which the client must not reconnect. */
const NO_RECONNECT_CODES = Object.freeze([4400, 4409]);

const BACKOFF_MS = Object.freeze([500, 1000, 2000, 4000, 5000]);
const JITTER_MS = 250;
const COALESCE_MS = 50;
/** An identical drive repeat is dropped only while the previous one is younger than this (feeds the drive timeout). */
const REFRESH_MS = 1000;
const STALE_FRAME_MS = 2000;
const CONNECT_STALE_MS = 5000;
const WATCHDOG_TICK_MS = 500;
const MOVE_TIMEOUT_SLACK_MS = 2000;
const MAX_BUFFERED_BYTES = 64 * 1024;
const MOVE_MS_MAX = 30000;

const SESSION_KEY = 'picar.clientId';
const MODE_KEY = 'picar.mode';

/**
 * Clamp a number to [lo, hi]; NaN becomes the fallback.
 * @param {*} value - anything
 * @param {number} lo - minimum
 * @param {number} hi - maximum
 * @param {number} [fallback] - result for NaN (default 0 clamped into range)
 * @returns {number} the clamped number (Infinity clamps to hi)
 */
const clamp = (value, lo, hi, fallback = 0) => {
    let n = Number(value);
    if (Number.isNaN(n)) n = fallback;
    return Math.min(hi, Math.max(lo, n));
};

/**
 * Build the WebSocket URL: `?picar_ws=` override, else same-origin /ws.
 * @param {{protocol?: string, host?: string, search?: string}} loc - a location-like object
 * @returns {string} the URL
 */
const defaultUrl = loc => {
    const search = (loc && loc.search) || '';
    const m = /[?&]picar_ws=([^&#]*)/.exec(search);
    if (m) {
        try {
            return decodeURIComponent(m[1]);
        } catch {
            return m[1];
        }
    }
    const scheme = loc && loc.protocol === 'https:' ? 'wss' : 'ws';
    return `${scheme}://${(loc && loc.host) || 'localhost'}/ws`;
};

/**
 * The REST estop URL that matches a WebSocket URL.
 * @param {string} wsUrl - the WebSocket URL
 * @returns {string} http(s) URL of POST /api/estop on the same origin
 */
const estopUrl = wsUrl => {
    const m = /^(wss?):\/\/([^/?#]+)/i.exec(wsUrl || '');
    if (!m) return '/api/estop';
    return `${m[1].toLowerCase() === 'wss' ? 'https' : 'http'}://${m[2]}/api/estop`;
};

module.exports = {
    PROTOCOL_VERSION,
    BYPASS_OPS,
    COALESCED_OPS,
    BACKPRESSURE_OPS,
    SWITCHING_OPS,
    NO_RECONNECT_CODES,
    BACKOFF_MS,
    JITTER_MS,
    COALESCE_MS,
    REFRESH_MS,
    STALE_FRAME_MS,
    CONNECT_STALE_MS,
    WATCHDOG_TICK_MS,
    MOVE_TIMEOUT_SLACK_MS,
    MAX_BUFFERED_BYTES,
    MOVE_MS_MAX,
    SESSION_KEY,
    MODE_KEY,
    clamp,
    defaultUrl,
    estopUrl
};
