import PropTypes from 'prop-types';
import React from 'react';
import classNames from 'classnames';

import EStopButton from '../components/car-monitor/estop-button.jsx';
import StatusChip from '../components/car-monitor/status-chip.jsx';
import {useCarLink, useCarState} from '../lib/picar/use-car-link.js';

import styles from '../components/car-monitor/car-monitor.css';

/**
 * E-STOP for places outside the monitor: the menu bar, and (with the status chip) floating over the
 * full-screen stage.
 * @param {object} props - props
 * @param {object} props.vm - the scratch-vm instance
 * @param {boolean} [props.floating] - float over the page with a status chip
 * @param {string} [props.className] - class for the button
 * @returns {?React.Element} the button, or null while the extension is not loaded
 */
const CarEstop = ({vm, floating, className}) => {
    const link = useCarLink(vm);
    const state = useCarState(link);
    if (!link) return null;
    if (floating) {
        return (
            <div className={styles.floating}>
                <StatusChip state={state} />
                <EStopButton link={link} />
            </div>
        );
    }
    return (
        <EStopButton
            link={link}
            className={classNames(className)}
        />
    );
};

CarEstop.propTypes = {
    className: PropTypes.string,
    floating: PropTypes.bool,
    vm: PropTypes.shape({})
};

export default CarEstop;
