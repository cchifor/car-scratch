/**
 * Pure helpers for the Car Monitor "room view": the obstacle map and the fixed camera.
 * Coordinates are cm, +x right, +y up (docs/contracts.md section 4).
 */
import {HUD_RESERVE_PX, carToWorld} from './car-renderer.js';

const OBSTACLE_MAX_CM = 200;
const OBSTACLE_GRID_CM = 3;
const OBSTACLE_MAX_POINTS = 3000;
// A pose jump larger than this between two frames is a reset or a teleport.
const POSE_JUMP_CM = 50;
const ROOM_MIN_W_CM = 300;
const ROOM_MIN_H_CM = 200;
const ROOM_MARGIN_CM = 20;
const ROOM_MARGIN_PX = 12;

/**
 * World position of the point an ultrasonic echo came from.
 * @param {{x: number, y: number, dir: number}} pose - rear-axle pose
 * @param {{ultrasonic_x: number}} geometry - normalised geometry
 * @param {number} cm - measured distance
 * @returns {{x: number, y: number}} hit point
 */
const obstaclePoint = (pose, geometry, cm) => carToWorld(pose, geometry.ultrasonic_x + cm, 0);

/**
 * @param {?object} dist - tel.dist
 * @returns {boolean} true when the reading yields a mappable obstacle
 */
const isMappableDist = dist => Boolean(dist) && dist.state === 'ok' &&
    typeof dist.cm === 'number' && isFinite(dist.cm) && dist.cm >= 0 && dist.cm <= OBSTACLE_MAX_CM;

/**
 * @param {?{x: number, y: number}} prev - previous pose
 * @param {{x: number, y: number, dir: number}} next - new pose
 * @returns {boolean} true when the pose jumped (reset_pose, teleport)
 */
const isPoseReset = (prev, next) => {
    if (!prev) return false;
    const d = Math.hypot(next.x - prev.x, next.y - prev.y);
    if (d > POSE_JUMP_CM) return true;
    // reset_pose lands exactly on the origin facing +x.
    return d > 3 && Math.hypot(next.x, next.y) < 0.05 && next.dir === 90;
};

/** Obstacle points on a 3 cm grid, oldest first, capped. Mutable on purpose (per-frame cost). */
class ObstacleMap {
    constructor () {
        this.points = [];
        this.keys = new Set();
    }

    clear () {
        this.points.length = 0;
        this.keys.clear();
    }

    /**
     * @param {{x: number, y: number}} p - world point
     * @returns {boolean} true when a new point was stored
     */
    addPoint (p) {
        const key = `${Math.round(p.x / OBSTACLE_GRID_CM)},${Math.round(p.y / OBSTACLE_GRID_CM)}`;
        if (this.keys.has(key)) return false;
        this.keys.add(key);
        this.points.push({x: p.x, y: p.y, key});
        if (this.points.length > OBSTACLE_MAX_POINTS) {
            const drop = this.points.splice(0, this.points.length - OBSTACLE_MAX_POINTS);
            for (const d of drop) this.keys.delete(d.key);
        }
        return true;
    }

    /**
     * @param {object} pose - pose the frame was taken at
     * @param {object} geometry - normalised geometry
     * @param {?object} dist - tel.dist
     * @returns {?{x: number, y: number}} the hit point when the reading was mappable
     */
    addReading (pose, geometry, dist) {
        if (!isMappableDist(dist)) return null;
        const p = obstaclePoint(pose, geometry, dist.cm);
        this.addPoint(p);
        return p;
    }
}

/**
 * @returns {{minX: number, maxX: number, minY: number, maxY: number}} bounds holding only the origin
 */
const newBounds = () => ({minX: 0, maxX: 0, minY: 0, maxY: 0});

/**
 * Grow the bounds to contain a point. Mutates and returns them; bounds never shrink.
 * @param {object} b - bounds
 * @param {{x: number, y: number}} p - point
 * @returns {object} b
 */
const growBounds = (b, p) => {
    if (p.x < b.minX) b.minX = p.x;
    if (p.x > b.maxX) b.maxX = p.x;
    if (p.y < b.minY) b.minY = p.y;
    if (p.y > b.maxY) b.maxY = p.y;
    return b;
};

/**
 * The fixed room camera: fits the bounds plus a margin, at least 300 x 200 cm.
 * @param {object} options - options
 * @param {number} options.width - canvas CSS width
 * @param {number} options.height - canvas CSS height
 * @param {object} options.bounds - growBounds result
 * @returns {object} view {cx, cy, scale, fixed, oy, box}; box is the cm area shown
 */
const computeRoomView = ({width, height: fullHeight, bounds}) => {
    const height = Math.max(1, fullHeight - HUD_RESERVE_PX);
    const oy = -HUD_RESERVE_PX / 2;
    const cx = (bounds.minX + bounds.maxX) / 2;
    const cy = (bounds.minY + bounds.maxY) / 2;
    const w = Math.max(ROOM_MIN_W_CM, (bounds.maxX - bounds.minX) + (2 * ROOM_MARGIN_CM));
    const h = Math.max(ROOM_MIN_H_CM, (bounds.maxY - bounds.minY) + (2 * ROOM_MARGIN_CM));
    const scale = Math.max(0.05, Math.min(
        (width - (2 * ROOM_MARGIN_PX)) / w,
        (height - (2 * ROOM_MARGIN_PX)) / h
    ));
    return {
        cx,
        cy,
        scale,
        fixed: true,
        oy,
        box: {minX: cx - (w / 2), maxX: cx + (w / 2), minY: cy - (h / 2), maxY: cy + (h / 2)}
    };
};

export {
    OBSTACLE_GRID_CM,
    OBSTACLE_MAX_CM,
    OBSTACLE_MAX_POINTS,
    POSE_JUMP_CM,
    ROOM_MIN_H_CM,
    ROOM_MIN_W_CM,
    ObstacleMap,
    computeRoomView,
    growBounds,
    isMappableDist,
    isPoseReset,
    newBounds,
    obstaclePoint
};
