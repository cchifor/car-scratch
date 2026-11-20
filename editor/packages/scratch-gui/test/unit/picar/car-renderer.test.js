import {
    CONE_MAX_CM,
    TRAIL_MAX_POINTS,
    advanceTreadPhase,
    angleDelta,
    carToWorld,
    computeView,
    coneStyle,
    distanceLevel,
    glowRadius,
    interpolatePose,
    lineSensorOffsets,
    normalizeGeometry,
    pushTrail,
    treadColor,
    treadDirection
} from '../../../src/components/car-monitor/car-renderer.js';

describe('car-renderer colour thresholds', () => {
    test('red below the margin, amber up to twice the margin, green beyond', () => {
        expect(distanceLevel(10, 24)).toBe('red');
        expect(distanceLevel(23.9, 24)).toBe('red');
        expect(distanceLevel(24, 24)).toBe('amber');
        expect(distanceLevel(48, 24)).toBe('amber');
        expect(distanceLevel(48.1, 24)).toBe('green');
    });
});

describe('car-renderer cone', () => {
    test('length follows the distance and is clamped', () => {
        expect(coneStyle({cm: 42, state: 'ok'}, 24).length).toBe(42);
        expect(coneStyle({cm: 500, state: 'ok'}, 24).length).toBe(CONE_MAX_CM);
    });
    test('far readings are faded and long', () => {
        const far = coneStyle({cm: null, state: 'far'}, 24);
        expect(far.kind).toBe('far');
        expect(far.length).toBe(CONE_MAX_CM);
        expect(far.alpha).toBeLessThan(0.2);
    });
    test('fault, none or missing data is hatched (unknown)', () => {
        expect(coneStyle({cm: null, state: 'fault'}, 24).kind).toBe('unknown');
        expect(coneStyle({cm: null, state: 'none'}, 24).kind).toBe('unknown');
        expect(coneStyle(null, 24).kind).toBe('unknown');
    });
    test('level colour matches the margin', () => {
        expect(coneStyle({cm: 10, state: 'ok'}, 24).level).toBe('red');
        expect(coneStyle({cm: 30, state: 'ok'}, 24).level).toBe('amber');
        expect(coneStyle({cm: 90, state: 'ok'}, 24).level).toBe('green');
    });
});

describe('car-renderer treads', () => {
    test('direction and colour', () => {
        expect(treadDirection({dir: 'forward', moving: true})).toBe('forward');
        expect(treadDirection({dir: 'backward', moving: true})).toBe('backward');
        expect(treadDirection({dir: null, moving: false})).toBe('stopped');
        expect(treadDirection(null)).toBe('stopped');
        expect(treadColor('forward')).toBe('#2E7D32');
        expect(treadColor('backward')).toBe('#EF6C00');
        expect(treadColor('stopped')).toBe('#78909C');
    });
    test('phase advances forward, reverses backward, freezes when stopped', () => {
        expect(advanceTreadPhase(0.2, 'forward', 50, 0.01)).toBeGreaterThan(0.2);
        expect(advanceTreadPhase(0.5, 'backward', 50, 0.01)).toBeLessThan(0.5);
        expect(advanceTreadPhase(0.5, 'stopped', 50, 1)).toBe(0.5);
    });
    test('phase wraps into [0, spacing)', () => {
        const wrapped = advanceTreadPhase(0.05, 'backward', 100, 0.1);
        expect(wrapped).toBeGreaterThanOrEqual(0);
        expect(wrapped).toBeLessThan(1);
    });
});

describe('car-renderer geometry', () => {
    test('carToWorld follows Scratch headings', () => {
        const east = carToWorld({x: 0, y: 0, dir: 90}, 10, 0);
        expect(east.x).toBeCloseTo(10);
        expect(east.y).toBeCloseTo(0);
        const northRight = carToWorld({x: 0, y: 0, dir: 0}, 0, 5);
        expect(northRight.x).toBeCloseTo(5);
        expect(northRight.y).toBeCloseTo(0);
        const eastRight = carToWorld({x: 0, y: 0, dir: 90}, 0, 5);
        expect(eastRight.x).toBeCloseTo(0);
        expect(eastRight.y).toBeCloseTo(-5);
    });
    test('line sensors are symmetric, sensor 1 leftmost', () => {
        const offsets = lineSensorOffsets(normalizeGeometry(null));
        expect(offsets).toHaveLength(5);
        expect(offsets[0].right).toBeLessThan(0);
        expect(offsets[2].right).toBeCloseTo(0);
        expect(offsets[4].right).toBeCloseTo(-offsets[0].right);
    });
    test('normalizeGeometry fills missing fields', () => {
        const g = normalizeGeometry({wheelbase: 20, body: {length: 30}});
        expect(g.wheelbase).toBe(20);
        expect(g.body.length).toBe(30);
        expect(g.body.width).toBe(16);
    });
    test('glow radius grows with brightness', () => {
        expect(glowRadius(100)).toBeGreaterThan(glowRadius(10));
    });
    test('angle interpolation takes the short way', () => {
        expect(angleDelta(350, 10)).toBe(20);
        expect(interpolatePose({x: 0, y: 0, dir: 350}, {x: 10, y: 0, dir: 10}, 0.5).dir).toBeCloseTo(360);
    });
    test('computeView fits a world and pans in car mode', () => {
        const fixed = computeView({width: 400, height: 300, world: {size: [200, 100]}, pose: {x: 5, y: 5}});
        expect(fixed.fixed).toBe(true);
        expect(fixed.scale).toBeCloseTo(368 / 200);
        const pan = computeView({width: 400, height: 300, world: null, pose: {x: 5, y: 7}});
        expect(pan.fixed).toBe(false);
        expect(pan.cx).toBe(5);
        expect(pan.cy).toBe(7);
    });
});

describe('car-renderer trail', () => {
    test('keeps at most TRAIL_MAX_POINTS', () => {
        const trail = [];
        for (let i = 0; i < TRAIL_MAX_POINTS + 50; i++) pushTrail(trail, {x: i * 1, y: 0});
        expect(trail.length).toBe(TRAIL_MAX_POINTS);
    });
    test('a teleport starts a new trail', () => {
        const trail = [];
        pushTrail(trail, {x: 0, y: 0});
        pushTrail(trail, {x: 1, y: 0});
        pushTrail(trail, {x: 500, y: 0});
        expect(trail).toEqual([{x: 500, y: 0}]);
    });
    test('tiny moves are ignored', () => {
        const trail = [];
        pushTrail(trail, {x: 0, y: 0});
        pushTrail(trail, {x: 0.1, y: 0});
        expect(trail.length).toBe(1);
    });
});
