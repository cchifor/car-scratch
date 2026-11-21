import {EventEmitter} from 'events';

/**
 * Minimal CarLink stand-in with the surface the editor uses (docs/contracts.md section 5).
 * @param {object} [stateOverrides] - fields merged into the initial state
 * @returns {object} an EventEmitter with jest mock methods
 */
const createMockLink = (stateOverrides = {}, {stageMirror = false} = {}) => {
    const link = new EventEmitter();
    let state = {
        connection: 'open',
        role: 'controller',
        mode: 'car',
        carAvailable: true,
        hw: {backend: 'real', capabilities: {ultrasonic: true, light: true, line: false}},
        config: {obstacle_stop: true, stop_cm: 20, speed_limit: 100},
        safety: {estop: false, hold: false, calibrated: true, blocked: false, hw_fault: null},
        world: null,
        worlds: [{id: 'arena', title: 'Arena'}, {id: 'corridor', title: 'Corridor'}],
        server: {version: '1.0.0'},
        lastError: null,
        ...stateOverrides
    };
    let tel = null;
    const subscribers = new Set();
    link.getState = () => state;
    link.getTelemetry = () => tel;
    link.subscribe = fn => {
        subscribers.add(fn);
        return () => subscribers.delete(fn);
    };
    link.estop = jest.fn();
    link.send = jest.fn(() => 1);
    link.control = jest.fn();
    link.setMode = jest.fn();
    if (stageMirror) link.setStageMirror = jest.fn();
    link.setState = patch => {
        state = {...state, ...patch};
        subscribers.forEach(fn => fn(state));
        link.emit('state', state);
    };
    link.emitTelemetry = patch => {
        tel = {
            t: 'tel',
            mode: state.mode,
            drive: {dir: null, speed: 0, moving: false},
            steer: 0,
            dist: {cm: 80, state: 'ok'},
            line: {ok: false, on: null},
            light: {ok: true, bright: [10, 20, 30], max: 'right'},
            pose: {x: 0, y: 0, dir: 90, src: 'dr'},
            safety: {estop: false, hold: false, calibrated: true, obstacle_stop: true, margin_cm: 24},
            ...patch
        };
        link.emit('telemetry', tel);
    };
    return link;
};

export {createMockLink};
