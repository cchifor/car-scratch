import PropTypes from 'prop-types';
import React, {useCallback, useEffect, useState} from 'react';
import classNames from 'classnames';
import {FormattedMessage, useIntl} from 'react-intl';

import EStopButton from '../components/car-monitor/estop-button.jsx';
import StatusChip from '../components/car-monitor/status-chip.jsx';
import messages from '../components/car-monitor/messages.js';
import {computeBanners, sameFlags, telemetryFlags} from '../components/car-monitor/monitor-flags.js';
import {useCarLink, useCarState} from '../lib/picar/use-car-link.js';

import styles from '../components/car-monitor/car-monitor.css';

/**
 * The telemetry flags the Car Monitor banners use, tracked from the link.
 * @param {?object} link - the CarLink
 * @param {?object} state - link state (re-evaluates when the session changes)
 * @returns {?object} flags
 */
const useTelemetryFlags = (link, state) => {
    const [flags, setFlags] = useState(null);
    useEffect(() => {
        if (!link) {
            setFlags(null);
            return;
        }
        setFlags(telemetryFlags(link.getTelemetry()));
        const onTelemetry = tel => {
            const next = telemetryFlags(tel);
            setFlags(prev => (sameFlags(prev, next) ? prev : next));
        };
        link.on('telemetry', onTelemetry);
        return () => link.removeListener('telemetry', onTelemetry);
    }, [link, state && state.connection, state && state.mode]);
    return flags;
};

/**
 * E-STOP for places outside the monitor: the menu bar, and (with the status chip) floating over the
 * full-screen stage. While the e-stop (or a hardware fault) is latched and this session may reset it,
 * a separate Reset button sits next to E-STOP; it sends the same estop_reset as the monitor banner.
 * @param {object} props - props
 * @param {object} props.vm - the scratch-vm instance
 * @param {boolean} [props.floating] - float over the page with a status chip
 * @param {string} [props.className] - class for the button
 * @returns {?React.Element} the button, or null while the extension is not loaded
 */
const CarEstop = ({vm, floating, className}) => {
    const intl = useIntl();
    const link = useCarLink(vm);
    const state = useCarState(link);
    const flags = useTelemetryFlags(link, state);
    const onReset = useCallback(() => {
        if (link) link.send('estop_reset', {});
    }, [link]);
    if (!link) return null;

    const banners = state ? computeBanners({state, flags, obstacleFlash: false, lineFault: false}) : [];
    const latchedBanner = banners.find(b => b.id === 'estop' || b.id === 'hwfault');
    const latched = Boolean(latchedBanner);
    const canReset = Boolean(latchedBanner && latchedBanner.action);
    const reset = canReset ? (
        <button
            type="button"
            className={styles.estopReset}
            aria-label={intl.formatMessage(messages.estopResetAria)}
            onClick={onReset}
        >
            <FormattedMessage {...messages.estopReset} />
        </button>
    ) : null;

    if (floating) {
        return (
            <div className={styles.floating}>
                <StatusChip state={state} />
                {reset}
                <EStopButton
                    link={link}
                    latched={latched}
                />
            </div>
        );
    }
    return (
        <React.Fragment>
            {reset}
            <EStopButton
                link={link}
                latched={latched}
                className={classNames(className)}
            />
        </React.Fragment>
    );
};

CarEstop.propTypes = {
    className: PropTypes.string,
    floating: PropTypes.bool,
    vm: PropTypes.shape({})
};

export default CarEstop;
