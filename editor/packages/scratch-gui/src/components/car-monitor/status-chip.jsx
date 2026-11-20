import classNames from 'classnames';
import PropTypes from 'prop-types';
import React from 'react';
import {FormattedMessage} from 'react-intl';

import messages from './messages.js';
import styles from './car-monitor.css';

const toneClass = tone => ({
    ok: styles.toneOk,
    warn: styles.toneWarn,
    bad: styles.toneBad,
    info: styles.toneInfo
})[tone];

/**
 * @param {?object} state - link.getState()
 * @returns {{tone: string, message: object}} chip colour tone and label
 */
const describeStatus = state => {
    const pending = !state || state.connection === 'connecting' || state.connection === 'switching';
    if (pending) return {tone: 'warn', message: messages.statusConnecting};
    if (state.connection === 'reload') return {tone: 'bad', message: messages.statusReload};
    if (state.connection !== 'open') return {tone: 'bad', message: messages.statusOffline};
    if (state.mode === 'sim') return {tone: 'ok', message: messages.statusSim};
    if (state.role === 'viewer') return {tone: 'info', message: messages.statusViewer};
    return {tone: 'ok', message: messages.statusController};
};

const StatusChip = ({state, className}) => {
    const {tone, message} = describeStatus(state);
    return (
        <span
            className={classNames(styles.chip, toneClass(tone), className)}
            data-tone={tone}
            role="status"
        >
            <span className={styles.chipDot} />
            <FormattedMessage {...message} />
        </span>
    );
};

StatusChip.propTypes = {
    className: PropTypes.string,
    state: PropTypes.shape({
        connection: PropTypes.string,
        mode: PropTypes.string,
        role: PropTypes.string
    })
};

export {StatusChip as default, describeStatus};
