import {DEFAULT_GEOMETRY} from '../../../src/components/car-monitor/car-renderer.js';
import {
    OBSTACLE_MAX_POINTS,
    ROOM_MIN_H_CM,
    ROOM_MIN_W_CM,
    ObstacleMap,
    computeRoomView,
    growBounds,
    isMappableDist,
    isPoseReset,
    newBounds,
    obstaclePoint
} from '../../../src/components/car-monitor/room-map.js';

const geo = {...DEFAULT_GEOMETRY, ultrasonic_x: 22};

describe('obstacle point math', () => {
    test('heading 90 points along +x from the sensor', () => {
        const p = obstaclePoint({x: 0, y: 0, dir: 90}, geo, 50);
        expect(p.x).toBeCloseTo(72, 6);
        expect(p.y).toBeCloseTo(0, 6);
    });
    test('heading 0 points along +y', () => {
        const p = obstaclePoint({x: 10, y: 5, dir: 0}, geo, 30);
        expect(p.x).toBeCloseTo(10, 6);
        expect(p.y).toBeCloseTo(57, 6);
    });
    test('sensor offset comes from the geometry', () => {
        const p = obstaclePoint({x: 0, y: 0, dir: 90}, {...geo, ultrasonic_x: 10}, 5);
        expect(p.x).toBeCloseTo(15, 6);
    });
    test('only ok readings up to 200 cm are mappable', () => {
        expect(isMappableDist({state: 'ok', cm: 200})).toBe(true);
        expect(isMappableDist({state: 'ok', cm: 201})).toBe(false);
        expect(isMappableDist({state: 'far', cm: 50})).toBe(false);
        expect(isMappableDist({state: 'ok', cm: null})).toBe(false);
        expect(isMappableDist(null)).toBe(false);
    });
});

describe('ObstacleMap', () => {
    const pose = {x: 0, y: 0, dir: 90};
    test('adds readings and dedupes on a 3 cm grid', () => {
        const map = new ObstacleMap();
        map.addReading(pose, geo, {state: 'ok', cm: 50});
        map.addReading(pose, geo, {state: 'ok', cm: 50.5});
        expect(map.points.length).toBe(1);
        map.addReading(pose, geo, {state: 'ok', cm: 60});
        expect(map.points.length).toBe(2);
    });
    test('ignores far, fault and long readings', () => {
        const map = new ObstacleMap();
        expect(map.addReading(pose, geo, {state: 'far', cm: null})).toBeNull();
        expect(map.addReading(pose, geo, {state: 'fault', cm: null})).toBeNull();
        expect(map.addReading(pose, geo, {state: 'ok', cm: 250})).toBeNull();
        expect(map.points.length).toBe(0);
    });
    test('caps at 3000 points and drops the oldest', () => {
        const map = new ObstacleMap();
        for (let i = 0; i < OBSTACLE_MAX_POINTS + 10; i++) map.addPoint({x: i * 10, y: 0});
        expect(map.points.length).toBe(OBSTACLE_MAX_POINTS);
        expect(map.points[0].x).toBe(100);
        // A dropped cell can be stored again.
        expect(map.addPoint({x: 0, y: 0})).toBe(true);
    });
    test('clear empties the map', () => {
        const map = new ObstacleMap();
        map.addPoint({x: 1, y: 1});
        map.clear();
        expect(map.points.length).toBe(0);
        expect(map.addPoint({x: 1, y: 1})).toBe(true);
    });
});

describe('pose reset detection', () => {
    test('a jump over 50 cm is a reset', () => {
        expect(isPoseReset({x: 100, y: 0, dir: 90}, {x: 0, y: 0, dir: 90})).toBe(true);
        expect(isPoseReset({x: 0, y: 0, dir: 90}, {x: 80, y: 0, dir: 0})).toBe(true);
    });
    test('reset_pose to the origin facing +x from a nearby pose is a reset', () => {
        expect(isPoseReset({x: 10, y: 0, dir: 45}, {x: 0, y: 0, dir: 90})).toBe(true);
    });
    test('normal driving and the first frame are not', () => {
        expect(isPoseReset({x: 0, y: 0, dir: 90}, {x: 2, y: 0, dir: 90})).toBe(false);
        expect(isPoseReset({x: 1, y: 0, dir: 90}, {x: 0, y: 0, dir: 90})).toBe(false);
        expect(isPoseReset(null, {x: 500, y: 0, dir: 90})).toBe(false);
    });
});

describe('room view bounds', () => {
    const view = b => computeRoomView({width: 500, height: 400, bounds: b});

    test('has at least 300 x 200 cm and contains the origin', () => {
        const v = view(newBounds());
        expect(v.box.maxX - v.box.minX).toBeGreaterThanOrEqual(ROOM_MIN_W_CM);
        expect(v.box.maxY - v.box.minY).toBeGreaterThanOrEqual(ROOM_MIN_H_CM);
        expect(v.box.minX).toBeLessThanOrEqual(0);
        expect(v.box.maxX).toBeGreaterThanOrEqual(0);
        expect(v.box.minY).toBeLessThanOrEqual(0);
        expect(v.box.maxY).toBeGreaterThanOrEqual(0);
    });
    test('contains the car, trail and obstacle points', () => {
        const b = newBounds();
        const pts = [{x: 400, y: 10}, {x: -250, y: -90}, {x: 30, y: 300}];
        pts.forEach(p => growBounds(b, p));
        const v = view(b);
        pts.concat([{x: 0, y: 0}]).forEach(p => {
            expect(p.x).toBeGreaterThanOrEqual(v.box.minX);
            expect(p.x).toBeLessThanOrEqual(v.box.maxX);
            expect(p.y).toBeGreaterThanOrEqual(v.box.minY);
            expect(p.y).toBeLessThanOrEqual(v.box.maxY);
        });
    });
    test('only grows: the scale never increases as points are added', () => {
        const b = newBounds();
        let last = view(b).scale;
        for (const p of [{x: 100, y: 0}, {x: 50, y: 20}, {x: 0, y: 0}, {x: -300, y: 40}, {x: 10, y: 10}]) {
            growBounds(b, p);
            const s = view(b).scale;
            expect(s).toBeLessThanOrEqual(last + 1e-9);
            last = s;
        }
    });
    test('a fresh bounds object starts small again (reset)', () => {
        const b = newBounds();
        growBounds(b, {x: 900, y: 900});
        const big = view(b).scale;
        expect(view(newBounds()).scale).toBeGreaterThan(big);
    });
});
