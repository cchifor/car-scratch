import classNames from 'classnames';
import PropTypes from 'prop-types';
import React, {useCallback, useEffect, useRef, useState} from 'react';
import {FormattedMessage, useIntl} from 'react-intl';

import {readSetting, writeSetting} from '../../lib/picar/storage.js';
import {useCarState} from '../../lib/picar/use-car-link.js';
import messages from './messages.js';
import MonitorBanners, {ObstacleOverlay} from './monitor-banners.jsx';
import MonitorCanvas from './monitor-canvas.jsx';
import {computeBanners, sameFlags, telemetryFlags} from './monitor-flags.js';
import PowerChip, {PowerDetails} from './power-chip.jsx';
import StatusChip from './status-chip.jsx';
import styles from './car-monitor.css';

const OBSTACLE_BANNER_MS = 2000;
const VIEW_KEY = 'picar.monitorView';

const initialView = () => (readSetting(VIEW_KEY) === 'follow' ? 'follow' : 'room');

const worldObject = world => (world && typeof world === 'object' ? world : null);
// "Arena (two obstacles)" -> "Arena" so the header stays a single row on a small iPad.
const shortWorldName = name => String(name).replace(/\s*\(.*\)\s*$/, '') || String(name);
const worldId = world => (typeof world === 'string' ? world : (world && world.id) || '');

/**
 * Car | Simulator switch.
 * @param {object} props - props
 * @param {?object} props.state - link state
 * @param {object} props.link - the CarLink
 * @returns {React.Element} the switch
 */
const ModeSwitch = ({state, link}) => {
    const intl = useIntl();
    const mode = state ? state.mode : null;
    const carDisabled = Boolean(state) && state.carAvailable === false;
    const pick = useCallback(next => () => link.setMode(next), [link]);
    return (
        <div
            className={styles.modeSwitch}
            role="group"
            aria-label={intl.formatMessage(messages.modeAria)}
        >
            <button
                type="button"
                className={classNames(styles.modeButton, {[styles.modeActive]: mode === 'car'})}
                aria-pressed={mode === 'car'}
                disabled={carDisabled}
                onClick={pick('car')}
            >
                <FormattedMessage {...messages.modeCar} />
            </button>
            <button
                type="button"
                className={classNames(styles.modeButton, {[styles.modeActive]: mode === 'sim'})}
                aria-pressed={mode === 'sim'}
                onClick={pick('sim')}
            >
                <FormattedMessage {...messages.modeSim} />
            </button>
        </div>
    );
};

ModeSwitch.propTypes = {
    link: PropTypes.shape({setMode: PropTypes.func}).isRequired,
    state: PropTypes.shape({
        carAvailable: PropTypes.bool,
        mode: PropTypes.string
    })
};

/**
 * Room | Follow switch.
 * @param {object} props - props
 * @param {string} props.view - 'room' or 'follow'
 * @param {function(string): void} props.onChange - called with the new view
 * @returns {React.Element} the switch
 */
const ViewSwitch = ({view, onChange}) => {
    const intl = useIntl();
    const pick = useCallback(next => () => onChange(next), [onChange]);
    return (
        <div
            className={styles.modeSwitch}
            role="group"
            aria-label={intl.formatMessage(messages.viewAria)}
        >
            <button
                type="button"
                className={classNames(styles.modeButton, {[styles.modeActive]: view === 'room'})}
                aria-pressed={view === 'room'}
                onClick={pick('room')}
            >
                <FormattedMessage {...messages.viewRoom} />
            </button>
            <button
                type="button"
                className={classNames(styles.modeButton, {[styles.modeActive]: view === 'follow'})}
                aria-pressed={view === 'follow'}
                onClick={pick('follow')}
            >
                <FormattedMessage {...messages.viewFollow} />
            </button>
        </div>
    );
};

ViewSwitch.propTypes = {
    onChange: PropTypes.func.isRequired,
    view: PropTypes.string.isRequired
};

/**
 * The Pi Car pane: header (mode, world, status, E-STOP), the animated canvas and the banners.
 * @param {object} props - props
 * @param {object} props.link - the CarLink (docs/contracts.md section 5)
 * @returns {React.Element} the monitor
 */
const CarMonitor = ({link}) => {
    const intl = useIntl();
    const state = useCarState(link);
    const [flags, setFlags] = useState(() => telemetryFlags(link.getTelemetry()));
    const [obstacleFlash, setObstacleFlash] = useState(false);
    const [lineFault, setLineFault] = useState(false);
    const [worldEvent, setWorldEvent] = useState(null);
    const [powerOpen, setPowerOpen] = useState(false);
    const [controllerPresent, setControllerPresent] = useState(null);
    const [view, setView] = useState(initialView);
    const [clearToken, setClearToken] = useState(0);
    const flashTimer = useRef(null);

    // Coarse flags from telemetry: React state changes only when a flag changes, never per frame.
    useEffect(() => {
        setFlags(telemetryFlags(link.getTelemetry()));
        const onTelemetry = tel => {
            const next = telemetryFlags(tel);
            setFlags(prev => (sameFlags(prev, next) ? prev : next));
        };
        link.on('telemetry', onTelemetry);
        return () => link.removeListener('telemetry', onTelemetry);
    }, [link, state && state.connection, state && state.mode]);

    useEffect(() => {
        const onEvent = evt => {
            if (!evt) return;
            if (evt.kind === 'obstacle_stop') {
                setObstacleFlash(true);
                clearTimeout(flashTimer.current);
                flashTimer.current = setTimeout(() => setObstacleFlash(false), OBSTACLE_BANNER_MS);
            } else if (evt.kind === 'sensor_fault' && evt.sensor === 'line') {
                setLineFault(true);
            } else if (evt.kind === 'sensor_ok' && evt.sensor === 'line') {
                setLineFault(false);
            } else if (evt.kind === 'estop' || evt.kind === 'estop_reset') {
                setObstacleFlash(false);
            }
        };
        const onWorld = world => setWorldEvent(world);
        const onRole = msg => setControllerPresent(msg && typeof msg.controller_present === 'boolean' ?
            msg.controller_present : null);
        link.on('event', onEvent);
        link.on('world', onWorld);
        link.on('role', onRole);
        return () => {
            link.removeListener('event', onEvent);
            link.removeListener('world', onWorld);
            link.removeListener('role', onRole);
            clearTimeout(flashTimer.current);
        };
    }, [link]);

    // A new session (reconnect, mode switch) starts without stale event banners.
    useEffect(() => {
        setLineFault(false);
        setObstacleFlash(false);
        setControllerPresent(null);
    }, [state && state.mode, state && state.connection]);

    const onAction = useCallback(action => {
        if (action === 'estopReset' || action === 'hwReset') {
            link.send('estop_reset', {});
        } else if (action === 'resume') {
            link.send('resume', {});
        } else if (action === 'take') {
            // Without a report that the car is free, assume someone is driving and ask first.
            const someoneDriving = controllerPresent !== false;
            const text = intl.formatMessage(messages.takeControlConfirm);
            if (!someoneDriving || confirm(text)) { // eslint-disable-line no-alert
                link.control('take', someoneDriving);
            }
        } else if (action === 'reload') {
            window.location.reload();
        }
    }, [link, intl, controllerPresent]);

    const onView = useCallback(next => {
        setView(next);
        writeSetting(VIEW_KEY, next);
    }, []);
    const onClearMap = useCallback(() => {
        setClearToken(n => n + 1);
        if (typeof link.clearTrail === 'function') link.clearTrail();
    }, [link]);
    const onResetPosition = useCallback(() => link.send('reset_pose', {}), [link]);
    const onStageMirror = useCallback(() => {
        const current = link.getState();
        link.setStageMirror(!(current && current.stageMirror));
    }, [link]);

    const togglePower = useCallback(() => setPowerOpen(open => !open), []);

    const onWorldChange = useCallback(event => {
        link.send('world', {world: event.target.value});
    }, [link]);

    const isSim = Boolean(state) && state.mode === 'sim';
    const caps = (state && state.hw && state.hw.capabilities) || {};
    const world = isSim ? (worldObject(state.world) || worldObject(worldEvent)) : null;
    const worlds = (state && state.worlds) || [];
    const currentWorld = worldId(state && state.world) || worldId(worldEvent);
    const canDrive = Boolean(state) && state.role !== 'viewer';
    const hasMirror = typeof link.setStageMirror === 'function';
    const mirrorOn = Boolean(state && state.stageMirror);
    const banners = computeBanners({state, flags, obstacleFlash, lineFault});

    return (
        <section
            className={styles.monitor}
            aria-label={intl.formatMessage(messages.monitorAria)}
        >
            <div className={styles.header}>
                {state ? (
                    <ModeSwitch
                        state={state}
                        link={link}
                    />
                ) : null}
                {isSim && worlds.length > 0 ? (
                    <select
                        className={styles.worldPicker}
                        aria-label={intl.formatMessage(messages.worldAria)}
                        value={currentWorld}
                        onChange={onWorldChange}
                    >
                        {worlds.map(w => (
                            <option
                                key={w.id}
                                value={w.id}
                                title={w.title || w.id}
                            >{shortWorldName(w.title || w.id)}</option>
                        ))}
                    </select>
                ) : null}
                {state && state.mode === 'car' ? (
                    <ViewSwitch
                        view={view}
                        onChange={onView}
                    />
                ) : null}
                <div className={styles.headerRight}>
                    <PowerChip
                        flags={flags}
                        open={powerOpen}
                        onToggle={togglePower}
                    />
                    <StatusChip state={state} />
                </div>
            </div>
            {powerOpen && flags && flags.powerSource ? <PowerDetails flags={flags} /> : null}
            <MonitorBanners
                banners={banners}
                onAction={onAction}
            />
            <div className={styles.stageArea}>
                <MonitorCanvas
                    link={link}
                    geometry={state && state.geometry}
                    world={world}
                    showLine={Boolean(caps.line) || lineFault}
                    resetKey={`${state ? state.mode : ''}|${state ? state.connection : ''}|${currentWorld}`}
                    viewMode={view}
                    clearToken={clearToken}
                />
                <div
                    className={styles.toolbar}
                    role="group"
                    aria-label={intl.formatMessage(messages.toolsAria)}
                >
                    <button
                        type="button"
                        className={styles.toolButton}
                        aria-label={intl.formatMessage(messages.clearMap)}
                        onClick={onClearMap}
                    >
                        <FormattedMessage {...messages.clearMapShort} />
                    </button>
                    {canDrive ? (
                        <button
                            type="button"
                            className={styles.toolButton}
                            aria-label={intl.formatMessage(messages.resetPosition)}
                            onClick={onResetPosition}
                        >
                            <FormattedMessage {...messages.resetShort} />
                        </button>
                    ) : null}
                    {hasMirror ? (
                        <button
                            type="button"
                            className={styles.toolButton}
                            aria-label={intl.formatMessage(messages.stageMirror)}
                            aria-pressed={mirrorOn}
                            onClick={onStageMirror}
                        >
                            <FormattedMessage {...messages.mirrorShort} />
                        </button>
                    ) : null}
                </div>
                <ObstacleOverlay banners={banners} />
            </div>
        </section>
    );
};

CarMonitor.propTypes = {
    link: PropTypes.shape({
        control: PropTypes.func,
        estop: PropTypes.func,
        getState: PropTypes.func.isRequired,
        getTelemetry: PropTypes.func.isRequired,
        on: PropTypes.func.isRequired,
        removeListener: PropTypes.func.isRequired,
        clearTrail: PropTypes.func,
        send: PropTypes.func,
        setMode: PropTypes.func,
        setStageMirror: PropTypes.func,
        subscribe: PropTypes.func.isRequired
    }).isRequired
};

export default CarMonitor;
