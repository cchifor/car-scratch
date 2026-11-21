import classNames from 'classnames';
import PropTypes from 'prop-types';
import React, {useCallback, useEffect, useRef, useState} from 'react';
import {FormattedMessage, useIntl} from 'react-intl';

import {useCarState} from '../../lib/picar/use-car-link.js';
import messages from './messages.js';
import MonitorBanners, {ObstacleOverlay} from './monitor-banners.jsx';
import MonitorCanvas from './monitor-canvas.jsx';
import {computeBanners, sameFlags, telemetryFlags} from './monitor-flags.js';
import StatusChip from './status-chip.jsx';
import styles from './car-monitor.css';

const OBSTACLE_BANNER_MS = 2000;

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
    const [controllerPresent, setControllerPresent] = useState(null);
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

    const onWorldChange = useCallback(event => {
        link.send('world', {world: event.target.value});
    }, [link]);

    const isSim = Boolean(state) && state.mode === 'sim';
    const caps = (state && state.hw && state.hw.capabilities) || {};
    const world = isSim ? (worldObject(state.world) || worldObject(worldEvent)) : null;
    const worlds = (state && state.worlds) || [];
    const currentWorld = worldId(state && state.world) || worldId(worldEvent);
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
                <StatusChip state={state} />
            </div>
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
                    resetKey={`${state ? state.mode : ''}|${state ? state.connection : ''}`}
                />
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
        send: PropTypes.func,
        setMode: PropTypes.func,
        subscribe: PropTypes.func.isRequired
    }).isRequired
};

export default CarMonitor;
