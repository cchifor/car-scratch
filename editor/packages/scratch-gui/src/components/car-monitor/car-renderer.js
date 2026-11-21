/**
 * Pure geometry and colour helpers for the Car Monitor canvas.
 *
 * Conventions (docs/contracts.md section 4): world and pose coordinates are in cm, +x right, +y up;
 * headings are Scratch degrees (0 = up, 90 = right, clockwise). In the car's local drawing frame
 * +x points forward and +y points to the car's right.
 */

const DEG = Math.PI / 180;

const TRAIL_MAX_POINTS = 2000;
const TRAIL_MIN_STEP_CM = 0.5;
// A pose jump larger than this between two telemetry frames is a teleport (reset / mode switch): start a new trail.
const TRAIL_JUMP_CM = 40;

const CONE_HALF_ANGLE_DEG = 15;
// Longest ultrasonic cone that is drawn; "far" readings and larger distances are clamped to it.
const CONE_MAX_CM = 100;
// Length of the hatched "unknown" cone shown for a fault or a missing echo.
const CONE_UNKNOWN_CM = 40;

const COLORS = {
    green: '#2E7D32',
    amber: '#F9A825',
    red: '#D32F2F',
    grey: '#78909C',
    orange: '#EF6C00'
};

const DEFAULT_GEOMETRY = {
    body: {length: 26, width: 16, rear_overhang: 4},
    wheelbase: 14,
    track: 13,
    ultrasonic_x: 22,
    line_x: 20,
    line_spacing: 2.0,
    light_x: 20,
    light_spread: 4
};

/**
 * @param {?object} geometry - welcome.geometry
 * @returns {object} geometry with every field present (missing fields use the default car)
 */
const normalizeGeometry = geometry => {
    const g = geometry || {};
    return {
        ...DEFAULT_GEOMETRY,
        ...g,
        body: {...DEFAULT_GEOMETRY.body, ...(g.body || {})}
    };
};

/**
 * @param {number} dir - Scratch heading in degrees
 * @returns {number} canvas rotation in radians that turns local +x (forward) to point along the heading
 */
const headingToCanvasAngle = dir => (dir - 90) * DEG;

/**
 * Map a point given in the car frame to world coordinates.
 * @param {{x: number, y: number, dir: number}} pose - rear-axle pose
 * @param {number} forward - cm ahead of the rear axle
 * @param {number} right - cm to the right of the car's centreline
 * @returns {{x: number, y: number}} world position in cm
 */
const carToWorld = (pose, forward, right) => {
    const s = Math.sin(pose.dir * DEG);
    const c = Math.cos(pose.dir * DEG);
    return {
        x: pose.x + (forward * s) + (right * c),
        y: pose.y + (forward * c) - (right * s)
    };
};

/**
 * @param {number} dist - tel.dist.cm
 * @param {number} marginCm - tel.safety.margin_cm
 * @returns {string} 'red' below the margin, 'amber' up to twice the margin, 'green' beyond
 */
const distanceLevel = (dist, marginCm) => {
    if (dist < marginCm) return 'red';
    if (dist <= 2 * marginCm) return 'amber';
    return 'green';
};

/**
 * Describe how the ultrasonic cone is drawn.
 * @param {?{cm: ?number, state: string}} dist - tel.dist
 * @param {number} marginCm - tel.safety.margin_cm
 * @returns {{kind: string, length: number, level: ?string, color: string, alpha: number}} kind is
 *   'ok' | 'far' | 'unknown' (hatched with a question mark)
 */
const coneStyle = (dist, marginCm) => {
    if (!dist || dist.state === 'fault' || dist.state === 'none' ||
        (dist.state === 'ok' && typeof dist.cm !== 'number')) {
        return {kind: 'unknown', length: CONE_UNKNOWN_CM, level: null, color: COLORS.grey, alpha: 0.5};
    }
    if (dist.state === 'far') {
        return {kind: 'far', length: CONE_MAX_CM, level: 'green', color: COLORS.green, alpha: 0.12};
    }
    const level = distanceLevel(dist.cm, marginCm);
    // Fade with range so a distant obstacle does not dominate the picture.
    const alpha = 0.55 - (0.3 * Math.min(1, dist.cm / CONE_MAX_CM));
    return {
        kind: 'ok',
        length: Math.min(Math.max(dist.cm, 0), CONE_MAX_CM),
        level,
        color: COLORS[level],
        alpha
    };
};

/**
 * @param {?{dir: ?string, moving?: boolean, speed?: number}} drive - tel.drive
 * @returns {string} 'forward' | 'backward' | 'stopped'
 */
const treadDirection = drive => {
    if (!drive || !drive.dir) return 'stopped';
    if (drive.moving === false) return 'stopped';
    return drive.dir === 'backward' ? 'backward' : 'forward';
};

/**
 * @param {string} direction - result of treadDirection
 * @returns {string} stripe colour: green forward, orange backward, grey stopped
 */
const treadColor = direction => {
    if (direction === 'forward') return COLORS.green;
    if (direction === 'backward') return COLORS.orange;
    return COLORS.grey;
};

/**
 * Rough ground speed for animating the tread stripes (purely visual, not a measurement).
 * @param {number} speed - user speed 0..100
 * @returns {number} cm per second
 */
const approxSpeedCmPerSec = speed => Math.max(0, Number(speed) || 0) * 0.4;

/**
 * Advance the tread stripe phase. Forward motion moves the stripes towards the rear of the wheel
 * (the phase grows), backward motion reverses them, a stopped car freezes them.
 * @param {number} phase - current phase in [0, spacing)
 * @param {string} direction - result of treadDirection
 * @param {number} speed - user speed 0..100
 * @param {number} dtSeconds - elapsed time
 * @param {number} [spacing] - stripe spacing in cm
 * @returns {number} new phase in [0, spacing)
 */
const advanceTreadPhase = (phase, direction, speed, dtSeconds, spacing = 1) => {
    if (direction === 'stopped') return phase;
    const sign = direction === 'backward' ? -1 : 1;
    const next = (phase + (sign * approxSpeedCmPerSec(speed) * dtSeconds)) % spacing;
    return next < 0 ? next + spacing : next;
};

/**
 * @param {number} bright - tel.light.bright[i] (0..100)
 * @returns {number} glow radius in cm
 */
const glowRadius = bright => 2 + ((Math.max(0, Math.min(100, Number(bright) || 0)) / 100) * 7);

/**
 * @param {string} max - tel.light.max
 * @returns {number} index into [left, middle, right], or -1 for none
 */
const brightestIndex = max => ['left', 'middle', 'right'].indexOf(max);

/**
 * Offsets of the n line sensors across the car, sensor 1 = leftmost (negative = left).
 * @param {object} geometry - normalised geometry
 * @param {number} [count] - number of sensors
 * @returns {Array<{forward: number, right: number}>} offsets in the car frame
 */
const lineSensorOffsets = (geometry, count = 5) => {
    const out = [];
    for (let i = 0; i < count; i++) {
        out.push({forward: geometry.line_x, right: (i - ((count - 1) / 2)) * geometry.line_spacing});
    }
    return out;
};

/**
 * Offsets of the left, middle and right light sensors.
 * @param {object} geometry - normalised geometry
 * @returns {Array<{forward: number, right: number}>} offsets in the car frame
 */
const lightSensorOffsets = geometry => [-1, 0, 1].map(k => ({
    forward: geometry.light_x,
    right: k * geometry.light_spread
}));

/**
 * @param {number} a - from heading in degrees
 * @param {number} b - to heading in degrees
 * @returns {number} signed shortest angular difference b - a in (-180, 180]
 */
const angleDelta = (a, b) => {
    let d = (b - a) % 360;
    if (d > 180) d -= 360;
    if (d <= -180) d += 360;
    return d;
};

/**
 * @param {?object} a - pose to start from
 * @param {object} b - pose to end at
 * @param {number} t - 0..1
 * @returns {object} interpolated pose (headings take the short way round)
 */
const interpolatePose = (a, b, t) => {
    if (!a) return b;
    const k = Math.max(0, Math.min(1, t));
    return {
        x: a.x + ((b.x - a.x) * k),
        y: a.y + ((b.y - a.y) * k),
        dir: a.dir + (angleDelta(a.dir, b.dir) * k)
    };
};

/**
 * Append a pose to the trail, keeping at most TRAIL_MAX_POINTS. A teleport starts a new trail.
 * Mutates and returns the array.
 * @param {Array<{x: number, y: number}>} trail - existing trail
 * @param {{x: number, y: number}} pose - newest pose
 * @returns {Array<{x: number, y: number}>} the same array
 */
const pushTrail = (trail, pose) => {
    const last = trail[trail.length - 1];
    if (last) {
        const d = Math.hypot(pose.x - last.x, pose.y - last.y);
        if (d > TRAIL_JUMP_CM) {
            trail.length = 0;
        } else if (d < TRAIL_MIN_STEP_CM) {
            return trail;
        }
    }
    trail.push({x: pose.x, y: pose.y});
    if (trail.length > TRAIL_MAX_POINTS) trail.splice(0, trail.length - TRAIL_MAX_POINTS);
    return trail;
};

/** Height in CSS px reserved at the bottom of the canvas for the HUD strip. */
const HUD_RESERVE_PX = 24;

/**
 * Choose the view (camera centre and scale) for the canvas.
 * @param {object} options - options
 * @param {number} options.width - canvas CSS width
 * @param {number} options.height - canvas CSS height
 * @param {?object} options.world - sim world (with size) or null in car mode
 * @param {{x: number, y: number}} options.pose - car pose (used in car mode)
 * @returns {{cx: number, cy: number, scale: number, fixed: boolean, oy: number}} centre in cm, scale in px per cm;
 *   fixed is true when the camera shows the whole world (no panning)
 */
const computeView = ({width, height: fullHeight, world, pose}) => {
    // The bottom HUD strip is reserved: the world never sits under it. oy shifts the view centre up.
    const height = Math.max(1, fullHeight - HUD_RESERVE_PX);
    const oy = -HUD_RESERVE_PX / 2;
    const minSide = Math.max(1, Math.min(width, height));
    if (world && Array.isArray(world.size) && world.size[0] > 0 && world.size[1] > 0) {
        const margin = 16;
        const scale = Math.max(0.1, Math.min(
            (width - (2 * margin)) / world.size[0],
            (height - (2 * margin)) / world.size[1]
        ));
        return {cx: 0, cy: 0, scale, fixed: true, oy};
    }
    // Car mode: about 110 cm across the shorter side, panning with the car.
    return {cx: pose.x, cy: pose.y, scale: minSide / 110, fixed: false, oy};
};

/**
 * Grid line positions (multiples of `step`) covering [min, max].
 * @param {number} min - lower bound
 * @param {number} max - upper bound
 * @param {number} step - spacing
 * @returns {number[]} positions
 */
const gridPositions = (min, max, step) => {
    const out = [];
    const first = Math.ceil(min / step);
    const last = Math.floor(max / step);
    for (let i = first; i <= last && out.length < 2000; i++) out.push(i * step);
    return out;
};

export {
    COLORS,
    CONE_HALF_ANGLE_DEG,
    CONE_MAX_CM,
    CONE_UNKNOWN_CM,
    DEFAULT_GEOMETRY,
    HUD_RESERVE_PX,
    TRAIL_MAX_POINTS,
    advanceTreadPhase,
    angleDelta,
    approxSpeedCmPerSec,
    brightestIndex,
    carToWorld,
    computeView,
    coneStyle,
    distanceLevel,
    glowRadius,
    gridPositions,
    headingToCanvasAngle,
    interpolatePose,
    lightSensorOffsets,
    lineSensorOffsets,
    normalizeGeometry,
    pushTrail,
    treadColor,
    treadDirection
};
