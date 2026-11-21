import classNames from 'classnames';
import PropTypes from 'prop-types';
import React from 'react';
import {FormattedMessage, useIntl} from 'react-intl';

import messages from './messages.js';
import styles from './car-monitor.css';

/**
 * @param {?object} flags - telemetryFlags
 * @returns {?{tone: string, message: object}} chip look, or null to hide the chip
 */
const describePower = flags => {
    if (!flags || !flags.powerSource) return null;
    if (flags.powerLow) return {tone: 'bad', message: messages.powerLow};
    if (flags.powerDipped) return {tone: 'warn', message: messages.powerDipped};
    if (flags.powerSource === 'pi') return {tone: 'ok', message: messages.powerOk};
    return {tone: 'idle', message: messages.powerSim};
};

const TONE = {ok: styles.powerOk, warn: styles.powerWarn, bad: styles.powerBad, idle: ''};

const BatteryIcon = ({low}) => (
    <svg
        className={styles.powerIcon}
        viewBox="0 0 24 24"
        aria-hidden="true"
        focusable="false"
    >
        <rect
            x="2"
            y="7"
            width="18"
            height="10"
            rx="2"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
        />
        <rect
            x="20.5"
            y="10"
            width="2"
            height="4"
            fill="currentColor"
        />
        <rect
            x="4.5"
            y="9.5"
            width={low ? 3 : 11}
            height="5"
            fill="currentColor"
        />
    </svg>
);

BatteryIcon.propTypes = {low: PropTypes.bool};

/**
 * Compact battery chip; a tap toggles the details panel. Renders nothing without power data.
 * @param {object} props - props
 * @param {?object} props.flags - telemetryFlags of the latest frame
 * @param {boolean} props.open - the details panel is open
 * @param {Function} props.onToggle - toggle the details panel
 * @returns {?React.Element} the chip
 */
const PowerChip = ({flags, open, onToggle}) => {
    const intl = useIntl();
    const look = describePower(flags);
    if (!look) return null;
    return (
        <button
            type="button"
            className={classNames(styles.powerChip, TONE[look.tone])}
            data-power={look.tone}
            aria-expanded={Boolean(open)}
            aria-label={`${intl.formatMessage(messages.powerAria)}: ${intl.formatMessage(look.message)}`}
            onClick={onToggle}
        >
            <BatteryIcon low={look.tone === 'bad'} />
            <FormattedMessage {...look.message} />
        </button>
    );
};

PowerChip.propTypes = {
    flags: PropTypes.shape({powerSource: PropTypes.string}),
    onToggle: PropTypes.func.isRequired,
    open: PropTypes.bool
};

/**
 * @param {object} props - props
 * @param {object} props.flags - telemetryFlags
 * @returns {React.Element} usage line and the help sentence
 */
const PowerDetails = ({flags}) => {
    const intl = useIntl();
    const parts = [];
    if (flags.powerOnMin !== null) parts.push(intl.formatMessage(messages.powerOn, {min: flags.powerOnMin}));
    if (flags.powerMotorMin !== null) {
        parts.push(intl.formatMessage(messages.powerMotors, {min: flags.powerMotorMin}));
    }
    if (flags.powerCpuTemp !== null) parts.push(intl.formatMessage(messages.powerCpu, {temp: flags.powerCpuTemp}));
    if (flags.powerLoadPct !== null) parts.push(intl.formatMessage(messages.powerLoad, {pct: flags.powerLoadPct}));
    return (
        <div
            className={styles.powerDetails}
            data-power-details=""
        >
            <div data-power-usage="">{parts.join(' \u00b7 ')}</div>
            <p className={styles.powerDetailsHelp}>
                <FormattedMessage {...messages.powerHelp} />
            </p>
        </div>
    );
};

PowerDetails.propTypes = {
    flags: PropTypes.shape({
        powerOnMin: PropTypes.number,
        powerMotorMin: PropTypes.number,
        powerCpuTemp: PropTypes.number,
        powerLoadPct: PropTypes.number
    }).isRequired
};

export {PowerChip as default, PowerDetails, describePower};
