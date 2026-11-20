/* global __PICAR_BUILD_SHA__ */
const ArgumentType = require('../../extension-support/argument-type');
const BlockType = require('../../extension-support/block-type');
const Cast = require('../../util/cast');
const formatMessage = require('format-message');
const CarLink = require('./car-link');
const P = require('./protocol');

/**
 * Icon (assets/branding/block-icon.svg) shown at the left edge of each block, as a data URI.
 * @type {string}
 */
// eslint-disable-next-line @stylistic/max-len
const blockIconURI = 'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI0MCIgaGVpZ2h0PSI0MCIgdmlld0JveD0iMCAwIDQwIDQwIiByb2xlPSJpbWciIGFyaWEtbGFiZWw9IlBpIENhciBibG9jayBpY29uIj4KICA8dGl0bGU+UGkgQ2FyPC90aXRsZT4KICA8ZyBmaWxsPSJub25lIiBzdHJva2U9IiNGRkZGRkYiIHN0cm9rZS13aWR0aD0iMi4yIiBzdHJva2UtbGluZWpvaW49InJvdW5kIiBzdHJva2UtbGluZWNhcD0icm91bmQiPgogICAgPHJlY3QgeD0iNSIgeT0iMTciIHdpZHRoPSIzMCIgaGVpZ2h0PSIxMCIgcng9IjMiLz4KICAgIDxwYXRoIGQ9Ik0xMiAxNyBMMTYgMTAuNSBMMjYgMTAuNSBMMzAgMTciLz4KICAgIDxjaXJjbGUgY3g9IjEyIiBjeT0iMjgiIHI9IjMuNSIvPgogICAgPGNpcmNsZSBjeD0iMjgiIGN5PSIyOCIgcj0iMy41Ii8+CiAgPC9nPgogIDxjaXJjbGUgY3g9IjM0IiBjeT0iMjEiIHI9IjEuNiIgZmlsbD0iI0ZGRkZGRiIvPgo8L3N2Zz4K';

/**
 * Palette category icon (40x40): the white car glyph on a red disc.
 * @type {string}
 */
// eslint-disable-next-line @stylistic/max-len
const menuIconURI = 'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI0MCIgaGVpZ2h0PSI0MCIgdmlld0JveD0iMCAwIDQwIDQwIiByb2xlPSJpbWciIGFyaWEtbGFiZWw9IlBpIENhciBjYXRlZ29yeSBpY29uIj48dGl0bGU+UGkgQ2FyPC90aXRsZT48Y2lyY2xlIGN4PSIyMCIgY3k9IjIwIiByPSIxOSIgZmlsbD0iI0M2MjgyOCIgc3Ryb2tlPSIjOEIxQjFCIiBzdHJva2Utd2lkdGg9IjIiLz48ZyB0cmFuc2Zvcm09InRyYW5zbGF0ZSgyMCAyMCkgc2NhbGUoMC41OCkgdHJhbnNsYXRlKC0yMCAtMjEpIj48ZyBmaWxsPSJub25lIiBzdHJva2U9IiNGRkZGRkYiIHN0cm9rZS13aWR0aD0iMi4yIiBzdHJva2UtbGluZWpvaW49InJvdW5kIiBzdHJva2UtbGluZWNhcD0icm91bmQiPjxyZWN0IHg9IjUiIHk9IjE3IiB3aWR0aD0iMzAiIGhlaWdodD0iMTAiIHJ4PSIzIi8+PHBhdGggZD0iTTEyIDE3IEwxNiAxMC41IEwyNiAxMC41IEwzMCAxNyIvPjxjaXJjbGUgY3g9IjEyIiBjeT0iMjgiIHI9IjMuNSIvPjxjaXJjbGUgY3g9IjI4IiBjeT0iMjgiIHI9IjMuNSIvPjwvZz48Y2lyY2xlIGN4PSIzNCIgY3k9IjIxIiByPSIxLjYiIGZpbGw9IiNGRkZGRkYiLz48L2c+PC9zdmc+';

const EXTENSION_ID = 'picar';
const FAR_CM = 999;
const STEER_DEG = {left: -30, straight: 0, right: 30};
const LINE_SENSORS = 5;
const LIGHT_INDEX = {left: 0, middle: 1, right: 2};

/**
 * Number from any Scratch value; NaN becomes 0, Infinity clamps.
 * @param value
 * @param lo
 * @param hi
 */
const num = (value, lo, hi) => P.clamp(Cast.toNumber(value), lo, hi);

/**
 * Scratch extension "Pi Car": drives the robot car (or its simulator) through a CarLink.
 */
class Scratch3PiCarBlocks {
    static get EXTENSION_ID () {
        return EXTENSION_ID;
    }

    /**
     * @param {Runtime} runtime - the Scratch runtime
     */
    constructor (runtime) {
        this.runtime = runtime;
        this.armed = false;
        this.link = null;
        this._prevConnection = 'closed';
        this._prevRole = 'viewer';
        this._prevMode = null;
        this._tripped = {estop: false, hw_fault: false};
        this._dropped = false;

        const factory = runtime.picarLinkFactory || Scratch3PiCarBlocks.linkFactory;
        if (factory) {
            this.link = factory(runtime);
        } else if (typeof WebSocket !== 'undefined' && typeof location !== 'undefined') {
            this.link = new CarLink({
                url: CarLink.defaultUrl(location),
                buildSha: typeof __PICAR_BUILD_SHA__ === 'undefined' ? null : __PICAR_BUILD_SHA__
            });
        }

        if (this.link) {
            const initial = this.link.getState();
            this._prevConnection = initial.connection;
            this._prevRole = initial.role;
            this._prevMode = initial.mode;
            this.link.on('event', evt => this._onLinkEvent(evt));
            this.link.on('state', state => this._onLinkState(state));
            if (/[?&]picar_debug=1(&|$|#)/.test((typeof location !== 'undefined' && location.search) || '')) {
                globalThis.__picar = {link: this.link, runtime};
            }
            this.link.start();
        }

        runtime.on('PROJECT_START', this._onProjectStart.bind(this));
        runtime.on('PROJECT_STOP_ALL', this._onProjectStopAll.bind(this));
        // Lookup only: no status button, no connection modal, no scan flow.
        runtime.registerPeripheralExtension(EXTENSION_ID, this);
    }

    // ---- peripheral lookup (no-ops) --------------------------------------------------------------

    scan () {}

    connect () {}

    disconnect () {}

    /**
     * @returns {boolean} whether the car link is open
     */
    isConnected () {
        return this._open();
    }

    // ---- runtime / link events -------------------------------------------------------------------

    _open () {
        return !!this.link && this.link.getState().connection === 'open';
    }

    _onProjectStart () {
        this.armed = true;
        if (this.link) this.link.send('resume');
    }

    _onProjectStopAll () {
        this.armed = false;
        if (this.link) this.link.send('stop');
    }

    _controlsCar () {
        const s = this.link.getState();
        return s.mode === 'sim' || s.role === 'controller';
    }

    _onLinkEvent (evt) {
        if (!evt) return;
        switch (evt.kind) {
        case 'estop':
        case 'hw_fault':
            // A server event arrives after the state it changed was already acted on (see _onLinkState).
            if (evt.local === true || !this._tripped[evt.kind]) this._safetyTripped(evt.kind, evt.local === true);
            break;
        case 'obstacle_stop':
            if (this.armed && this._open()) this._startEventHats('obstacle');
            break;
        default:
        }
    }

    /**
     * The car went into estop / hardware fault: stop the project (an operator pressing E-STOP always does).
     * @param {string} kind - 'estop' or 'hw_fault'
     * @param {boolean} local - this browser pressed E-STOP
     */
    _safetyTripped (kind, local) {
        const wasArmed = this.armed;
        if (local || this._controlsCar()) this.runtime.stopAll();
        if (kind === 'estop' && wasArmed) this._startEventHats('estop');
    }

    _onLinkState (state) {
        const prev = this._prevConnection;
        const prevRole = this._prevRole;
        const prevMode = this._prevMode;
        this._prevConnection = state.connection;
        this._prevRole = state.role;
        this._prevMode = state.mode;
        const wasUp = prev === 'open' || prev === 'switching';
        const down = state.connection === 'closed' || state.connection === 'reload';
        if (wasUp && down && this.armed) {
            this._startEventHats('disconnected');
        }
        if (wasUp && down) this._dropped = true;

        // The server starts every acquisition of car control on hold; a running project must not carry on into it.
        let stop = false;
        if (state.connection === 'open' && this._dropped) {
            this._dropped = false;
            stop = true;
        }
        if (prevRole === 'viewer' && state.role === 'controller' && prev !== 'connecting') stop = true;
        if (prevMode !== null && prevMode !== 'car' && state.mode === 'car') stop = true;
        if (stop) this.runtime.stopAll();

        // A welcome or tel frame that reports estop / hardware fault for the first time.
        if (state.safety) {
            for (const kind of Object.keys(this._tripped)) {
                const now = Boolean(state.safety[kind]);
                if (now && !this._tripped[kind]) {
                    this._tripped[kind] = true;
                    this._safetyTripped(kind, false);
                } else if (!now) {
                    this._tripped[kind] = false;
                }
            }
        }
    }

    _startEventHats (event) {
        // startHats mutates the match object, so every call gets a fresh one.
        this.runtime.startHats('picar_whenCarEvent', {EVENT: event});
    }

    // ---- block metadata --------------------------------------------------------------------------

    getInfo () {
        return {
            id: EXTENSION_ID,
            name: formatMessage({
                id: 'picar.categoryName',
                default: 'Pi Car',
                description: 'Name of the Pi Car extension category'
            }),
            color1: '#C62828',
            color2: '#A52121',
            color3: '#8B1B1B',
            blockIconURI,
            menuIconURI,
            blocks: [
                {
                    opcode: 'drive',
                    blockType: BlockType.COMMAND,
                    text: formatMessage({
                        id: 'picar.drive',
                        default: 'drive [DIR] at [SPEED] %',
                        description: 'drive the car in a direction at a speed until told otherwise'
                    }),
                    arguments: {
                        DIR: {type: ArgumentType.STRING, menu: 'dir', defaultValue: 'forward'},
                        SPEED: {type: ArgumentType.NUMBER, defaultValue: 50}
                    }
                },
                {
                    opcode: 'driveFor',
                    blockType: BlockType.COMMAND,
                    text: formatMessage({
                        id: 'picar.driveFor',
                        default: 'drive [DIR] at [SPEED] % for [SECS] seconds',
                        description: 'drive the car for a number of seconds'
                    }),
                    arguments: {
                        DIR: {type: ArgumentType.STRING, menu: 'dir', defaultValue: 'forward'},
                        SPEED: {type: ArgumentType.NUMBER, defaultValue: 50},
                        SECS: {type: ArgumentType.NUMBER, defaultValue: 1}
                    }
                },
                {
                    opcode: 'stopMotors',
                    blockType: BlockType.COMMAND,
                    text: formatMessage({
                        id: 'picar.stopMotors',
                        default: 'stop motors',
                        description: 'stop the car'
                    })
                },
                {
                    opcode: 'setSpeed',
                    blockType: BlockType.COMMAND,
                    text: formatMessage({
                        id: 'picar.setSpeed',
                        default: 'set speed to [SPEED] %',
                        description: 'change the speed of the current drive'
                    }),
                    arguments: {
                        SPEED: {type: ArgumentType.NUMBER, defaultValue: 50}
                    }
                },
                {
                    opcode: 'steer',
                    blockType: BlockType.COMMAND,
                    text: formatMessage({
                        id: 'picar.steer',
                        default: 'steer [STEER]',
                        description: 'steer left, straight or right'
                    }),
                    arguments: {
                        STEER: {type: ArgumentType.STRING, menu: 'steer', defaultValue: 'straight'}
                    }
                },
                {
                    opcode: 'setSteering',
                    blockType: BlockType.COMMAND,
                    text: formatMessage({
                        id: 'picar.setSteering',
                        default: 'set steering angle to [ANGLE] °',
                        description: 'set the steering angle in degrees'
                    }),
                    arguments: {
                        ANGLE: {type: ArgumentType.NUMBER, defaultValue: 0}
                    }
                },
                '---',
                {
                    opcode: 'distance',
                    blockType: BlockType.REPORTER,
                    text: formatMessage({
                        id: 'picar.distance',
                        default: 'distance (cm)',
                        description: 'distance to the nearest obstacle ahead'
                    })
                },
                {
                    opcode: 'obstacleCloser',
                    blockType: BlockType.BOOLEAN,
                    text: formatMessage({
                        id: 'picar.obstacleCloser',
                        default: 'obstacle closer than [CM] cm?',
                        description: 'is there an obstacle closer than a distance'
                    }),
                    arguments: {
                        CM: {type: ArgumentType.NUMBER, defaultValue: 20}
                    }
                },
                {
                    opcode: 'whenObstacle',
                    blockType: BlockType.HAT,
                    isEdgeActivated: true,
                    text: formatMessage({
                        id: 'picar.whenObstacle',
                        default: 'when obstacle closer than [CM] cm',
                        description: 'hat that fires when an obstacle comes closer than a distance'
                    }),
                    arguments: {
                        CM: {type: ArgumentType.NUMBER, defaultValue: 20}
                    }
                },
                '---',
                {
                    opcode: 'lineValue',
                    blockType: BlockType.REPORTER,
                    text: formatMessage({
                        id: 'picar.lineValue',
                        default: 'line sensor [SENSOR] value',
                        description: 'raw value of a line sensor'
                    }),
                    arguments: {
                        SENSOR: {type: ArgumentType.NUMBER, menu: 'sensor', defaultValue: '1'}
                    }
                },
                {
                    opcode: 'lineSees',
                    blockType: BlockType.BOOLEAN,
                    text: formatMessage({
                        id: 'picar.lineSees',
                        default: 'line sensor [SENSOR] sees line?',
                        description: 'does a line sensor see the line'
                    }),
                    arguments: {
                        SENSOR: {type: ArgumentType.NUMBER, menu: 'sensor', defaultValue: '3'}
                    }
                },
                {
                    opcode: 'linePosition',
                    blockType: BlockType.REPORTER,
                    text: formatMessage({
                        id: 'picar.linePosition',
                        default: 'line position',
                        description: 'where the line is: -2 (left) to 2 (right)'
                    })
                },
                {
                    opcode: 'whenLineSees',
                    blockType: BlockType.HAT,
                    isEdgeActivated: true,
                    text: formatMessage({
                        id: 'picar.whenLineSees',
                        default: 'when line sensor [SENSOR] sees line',
                        description: 'hat that fires when a line sensor sees the line'
                    }),
                    arguments: {
                        SENSOR: {type: ArgumentType.NUMBER, menu: 'sensor', defaultValue: '3'}
                    }
                },
                '---',
                {
                    opcode: 'lightValue',
                    blockType: BlockType.REPORTER,
                    text: formatMessage({
                        id: 'picar.lightValue',
                        default: '[LIGHT] light brightness',
                        description: 'brightness (0 to 100) seen by a light sensor'
                    }),
                    arguments: {
                        LIGHT: {type: ArgumentType.STRING, menu: 'light', defaultValue: 'middle'}
                    }
                },
                {
                    opcode: 'brightestDirection',
                    blockType: BlockType.REPORTER,
                    text: formatMessage({
                        id: 'picar.brightestDirection',
                        default: 'brightest light direction',
                        description: 'which light sensor sees the most light'
                    })
                },
                '---',
                {
                    opcode: 'setObstacleStop',
                    blockType: BlockType.COMMAND,
                    text: formatMessage({
                        id: 'picar.setObstacleStop',
                        default: 'turn obstacle stop [ONOFF]',
                        description: 'turn the automatic obstacle stop on or off'
                    }),
                    arguments: {
                        ONOFF: {type: ArgumentType.STRING, menu: 'onoff', defaultValue: 'on'}
                    }
                },
                {
                    opcode: 'setStopDistance',
                    blockType: BlockType.COMMAND,
                    text: formatMessage({
                        id: 'picar.setStopDistance',
                        default: 'set obstacle stop distance to [CM] cm',
                        description: 'set the distance at which the car stops for an obstacle'
                    }),
                    arguments: {
                        CM: {type: ArgumentType.NUMBER, defaultValue: 20}
                    }
                },
                {
                    opcode: 'setSpeedLimit',
                    blockType: BlockType.COMMAND,
                    text: formatMessage({
                        id: 'picar.setSpeedLimit',
                        default: 'set speed limit to [PERCENT] %',
                        description: 'set the highest speed the car may drive'
                    }),
                    arguments: {
                        PERCENT: {type: ArgumentType.NUMBER, defaultValue: 100}
                    }
                },
                {
                    opcode: 'emergencyStop',
                    blockType: BlockType.COMMAND,
                    text: formatMessage({
                        id: 'picar.emergencyStop',
                        default: 'emergency stop',
                        description: 'latched emergency stop'
                    })
                },
                '---',
                {
                    opcode: 'isConnected',
                    blockType: BlockType.BOOLEAN,
                    text: formatMessage({
                        id: 'picar.isConnected',
                        default: 'car connected?',
                        description: 'is the car (or simulator) connected'
                    })
                },
                {
                    opcode: 'carMode',
                    blockType: BlockType.REPORTER,
                    text: formatMessage({
                        id: 'picar.carMode',
                        default: 'car mode',
                        description: 'car, sim or offline'
                    })
                },
                {
                    opcode: 'carX',
                    blockType: BlockType.REPORTER,
                    text: formatMessage({
                        id: 'picar.carX',
                        default: 'car x',
                        description: 'x position of the car in cm'
                    })
                },
                {
                    opcode: 'carY',
                    blockType: BlockType.REPORTER,
                    text: formatMessage({
                        id: 'picar.carY',
                        default: 'car y',
                        description: 'y position of the car in cm'
                    })
                },
                {
                    opcode: 'carDirection',
                    blockType: BlockType.REPORTER,
                    text: formatMessage({
                        id: 'picar.carDirection',
                        default: 'car direction',
                        description: 'heading of the car in degrees'
                    })
                },
                {
                    opcode: 'resetPosition',
                    blockType: BlockType.COMMAND,
                    text: formatMessage({
                        id: 'picar.resetPosition',
                        default: 'reset car position',
                        description: 'reset the car position to the start'
                    })
                },
                {
                    opcode: 'whenCarEvent',
                    blockType: BlockType.HAT,
                    isEdgeActivated: false,
                    shouldRestartExistingThreads: false,
                    text: formatMessage({
                        id: 'picar.whenCarEvent',
                        default: 'when car [EVENT]',
                        description: 'hat that fires when something happens to the car'
                    }),
                    arguments: {
                        EVENT: {type: ArgumentType.STRING, menu: 'event', defaultValue: 'obstacle'}
                    }
                }
            ],
            menus: {
                dir: {
                    acceptReporters: false,
                    items: [
                        {
                            text: formatMessage({
                                id: 'picar.dir.forward', default: 'forward', description: 'drive direction'
                            }),
                            value: 'forward'
                        },
                        {
                            text: formatMessage({
                                id: 'picar.dir.backward', default: 'backward', description: 'drive direction'
                            }),
                            value: 'backward'
                        }
                    ]
                },
                steer: {
                    acceptReporters: false,
                    items: [
                        {
                            text: formatMessage({
                                id: 'picar.steer.left', default: 'left', description: 'steer direction'
                            }),
                            value: 'left'
                        },
                        {
                            text: formatMessage({
                                id: 'picar.steer.straight', default: 'straight', description: 'steer direction'
                            }),
                            value: 'straight'
                        },
                        {
                            text: formatMessage({
                                id: 'picar.steer.right', default: 'right', description: 'steer direction'
                            }),
                            value: 'right'
                        }
                    ]
                },
                sensor: {
                    acceptReporters: true,
                    items: ['1', '2', '3', '4', '5'].map(v => ({text: v, value: v}))
                },
                light: {
                    acceptReporters: false,
                    items: [
                        {
                            text: formatMessage({
                                id: 'picar.light.left', default: 'left', description: 'light sensor position'
                            }),
                            value: 'left'
                        },
                        {
                            text: formatMessage({
                                id: 'picar.light.middle', default: 'middle', description: 'light sensor position'
                            }),
                            value: 'middle'
                        },
                        {
                            text: formatMessage({
                                id: 'picar.light.right', default: 'right', description: 'light sensor position'
                            }),
                            value: 'right'
                        }
                    ]
                },
                onoff: {
                    acceptReporters: false,
                    items: [
                        {
                            text: formatMessage({id: 'picar.onoff.on', default: 'on', description: 'switch on'}),
                            value: 'on'
                        },
                        {
                            text: formatMessage({id: 'picar.onoff.off', default: 'off', description: 'switch off'}),
                            value: 'off'
                        }
                    ]
                },
                event: {
                    acceptReporters: false,
                    items: [
                        {
                            text: formatMessage({
                                id: 'picar.event.obstacle',
                                default: 'stops for an obstacle',
                                description: 'car event'
                            }),
                            value: 'obstacle'
                        },
                        {
                            text: formatMessage({
                                id: 'picar.event.estop',
                                default: 'is emergency stopped',
                                description: 'car event'
                            }),
                            value: 'estop'
                        },
                        {
                            text: formatMessage({
                                id: 'picar.event.disconnected',
                                default: 'loses connection',
                                description: 'car event'
                            }),
                            value: 'disconnected'
                        }
                    ]
                }
            }
        };
    }

    // ---- commands --------------------------------------------------------------------------------

    drive (args) {
        if (!this.link) return;
        const speed = Math.round(num(args.SPEED, 0, 100));
        if (speed === 0) {
            this.link.send('stop');
            return;
        }
        this.link.send('drive', {dir: args.DIR === 'backward' ? 'backward' : 'forward', speed});
    }

    driveFor (args) {
        if (!this.link) return;
        const secs = num(args.SECS, 0, P.MOVE_MS_MAX / 1000);
        if (!(secs > 0)) return;
        const ms = Math.max(1, Math.round(secs * 1000));
        return this.link.move(args.DIR === 'backward' ? 'backward' : 'forward',
            Math.round(num(args.SPEED, 0, 100)), ms);
    }

    stopMotors () {
        if (this.link) this.link.send('stop');
    }

    setSpeed (args) {
        if (this.link) this.link.send('set_speed', {speed: Math.round(num(args.SPEED, 0, 100))});
    }

    steer (args) {
        if (!this.link) return;
        const angle = Object.prototype.hasOwnProperty.call(STEER_DEG, args.STEER) ? STEER_DEG[args.STEER] : 0;
        this.link.send('steer', {angle});
    }

    setSteering (args) {
        if (this.link) this.link.send('steer', {angle: num(args.ANGLE, -45, 45)});
    }

    setObstacleStop (args) {
        if (this.link) this.link.send('config', {obstacle_stop: args.ONOFF !== 'off'});
    }

    setStopDistance (args) {
        if (this.link) this.link.send('config', {stop_cm: num(args.CM, 10, 100)});
    }

    setSpeedLimit (args) {
        if (this.link) this.link.send('config', {speed_limit: Math.round(num(args.PERCENT, 1, 100))});
    }

    emergencyStop () {
        if (this.link) this.link.estop();
    }

    resetPosition () {
        if (this.link) this.link.send('reset_pose');
    }

    // ---- reporters -------------------------------------------------------------------------------

    _tel () {
        return this._open() ? this.link.getTelemetry() : null;
    }

    _sensorIndex (value) {
        const n = Math.round(Cast.toNumber(value));
        return n >= 1 && n <= LINE_SENSORS ? n - 1 : -1;
    }

    distance () {
        const t = this._tel();
        const d = t && t.dist;
        if (!d) return FAR_CM;
        if (d.state === 'fault') return 0;
        if (d.state === 'ok' && typeof d.cm === 'number') return Math.round(d.cm * 10) / 10;
        return FAR_CM;
    }

    obstacleCloser (args) {
        const t = this._tel();
        const d = t && t.dist;
        if (!d) return false;
        if (d.state === 'fault') return true;
        return d.state === 'ok' && typeof d.cm === 'number' && d.cm < Cast.toNumber(args.CM);
    }

    lineValue (args) {
        const t = this._tel();
        const i = this._sensorIndex(args.SENSOR);
        const raw = t && t.line && t.line.raw;
        return i >= 0 && Array.isArray(raw) && typeof raw[i] === 'number' ? raw[i] : 0;
    }

    lineSees (args) {
        const t = this._tel();
        const i = this._sensorIndex(args.SENSOR);
        const on = t && t.line && t.line.on;
        return i >= 0 && Array.isArray(on) && on[i] === true;
    }

    linePosition () {
        const t = this._tel();
        const pos = t && t.line && t.line.pos;
        return typeof pos === 'number' ? pos : 0;
    }

    lightValue (args) {
        const t = this._tel();
        const i = Object.prototype.hasOwnProperty.call(LIGHT_INDEX, args.LIGHT) ? LIGHT_INDEX[args.LIGHT] : -1;
        const bright = t && t.light && t.light.bright;
        return i >= 0 && Array.isArray(bright) && typeof bright[i] === 'number' ? bright[i] : 0;
    }

    brightestDirection () {
        const t = this._tel();
        const max = t && t.light && t.light.max;
        return typeof max === 'string' ? max : 'none';
    }

    carMode () {
        return this._open() ? this.link.getState().mode : 'offline';
    }

    _pose (key, fallback) {
        const t = this._tel();
        const v = t && t.pose && t.pose[key];
        return typeof v === 'number' ? Math.round(v * 10) / 10 : fallback;
    }

    carX () {
        return this._pose('x', 0);
    }

    carY () {
        return this._pose('y', 0);
    }

    carDirection () {
        return this._pose('dir', 90);
    }

    // ---- hats ------------------------------------------------------------------------------------

    whenObstacle (args) {
        return this.armed && this._open() && this.obstacleCloser(args);
    }

    whenLineSees (args) {
        return this.armed && this._open() && this.lineSees(args);
    }

    whenCarEvent () {
        return true;
    }
}

/**
 * Test hook: a function (runtime) => link used instead of the browser-global CarLink.
 * @type {?Function}
 */
Scratch3PiCarBlocks.linkFactory = null;

module.exports = Scratch3PiCarBlocks;
