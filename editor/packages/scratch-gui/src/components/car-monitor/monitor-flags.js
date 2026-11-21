const num = v => (typeof v === 'number' && isFinite(v) ? v : null);

/**
 * Flat, primitive power flags (so sameFlags only re-renders when something visible changes).
 * An older server sends no power section: powerSource is then null and the chip is hidden.
 * @param {?object} power - tel.power
 * @returns {object} flags
 */
const powerFlags = power => {
    const p = power && typeof power === 'object' ? power : {};
    const source = p.source === 'pi' || p.source === 'sim' ? p.source : null;
    const onS = num(p.on_s);
    const motorS = num(p.motor_s);
    const temp = num(p.cpu_temp_c);
    const load = num(p.cpu_load);
    return {
        powerSource: source,
        powerLow: source !== null && p.low === true,
        powerDipped: source !== null && (p.low_since_boot === true || num(p.low_events) > 0),
        powerOnMin: onS === null ? null : Math.round(onS / 60),
        powerMotorMin: motorS === null ? null : Math.round(motorS / 60),
        powerCpuTemp: temp === null ? null : Math.round(temp),
        powerLoadPct: load === null ? null : Math.round(load * 100)
    };
};

/**
 * Reduce a telemetry frame to the coarse flags the banners need. The Car Monitor compares these
 * after every frame and re-renders React only when one changes.
 * @param {?object} tel - latest telemetry or null
 * @returns {?object} flags, or null without telemetry
 */
const telemetryFlags = tel => {
    if (!tel) return null;
    const safety = tel.safety || {};
    const line = tel.line || {};
    return {
        estop: Boolean(safety.estop),
        hold: Boolean(safety.hold),
        hwFault: Boolean(safety.hw_fault),
        calibrated: typeof safety.calibrated === 'boolean' ? safety.calibrated : null,
        obstacleStop: typeof safety.obstacle_stop === 'boolean' ? safety.obstacle_stop : null,
        lineOk: typeof line.ok === 'boolean' ? line.ok : null,
        ...powerFlags(tel.power)
    };
};

const sameFlags = (a, b) => {
    if (a === b) return true;
    if (!a || !b) return false;
    return Object.keys(a).every(key => a[key] === b[key]);
};

/**
 * Decide which banners to show.
 * @param {object} input - input
 * @param {?object} input.state - link.getState()
 * @param {?object} input.flags - telemetryFlags of the latest frame
 * @param {boolean} input.obstacleFlash - an obstacle stop event happened in the last 2 s
 * @param {boolean} input.lineFault - the line sensor reported a fault event
 * @returns {Array<{id: string, tone: string, small: boolean, action: ?string}>} banners, most urgent first
 */
const computeBanners = ({state, flags, obstacleFlash, lineFault}) => {
    const out = [];
    const add = (id, tone, small = false, action = null) => out.push({id, tone, small, action});
    const connection = state ? state.connection : 'connecting';

    if (connection === 'switching') return out;
    if (connection === 'reload') {
        add('reload', 'bad', false, 'reload');
        return out;
    }
    if (connection !== 'open') {
        add('reconnecting', 'warn');
        return out;
    }

    const isController = state.role !== 'viewer';
    const isCar = state.mode !== 'sim';
    const f = flags || {};

    if (state.mode === 'car' && state.carAvailable === false) add('noCar', 'bad', true);
    if (f.hwFault || Boolean(state.safety && state.safety.hw_fault)) {
        add('hwfault', 'bad', false, isController ? 'hwReset' : null);
    }
    if (f.estop) add('estop', 'bad', false, isController ? 'estopReset' : null);
    else if (f.hold) add('paused', 'warn', false, isController ? 'resume' : null);
    if (obstacleFlash) add('obstacle', 'bad');
    if (f.powerLow) add('batteryLow', 'bad');
    if (!isController) add('viewOnly', 'info', false, 'take');
    if (f.obstacleStop === false) add('obstacleStopOff', 'bad', true);
    if (isCar && f.calibrated === false) add('notCalibrated', 'warn', true);
    const caps = state.hw && state.hw.capabilities;
    if (lineFault || (caps && caps.line && f.lineOk === false)) add('lineOffline', 'warn', true);
    return out;
};

export {computeBanners, powerFlags, sameFlags, telemetryFlags};
