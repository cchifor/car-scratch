import PropTypes from 'prop-types';
import React from 'react';
import {connect} from 'react-redux';
import VM from '@scratch/scratch-vm';

import CarMonitorComponent from '../components/car-monitor/car-monitor.jsx';
import {useCarLink} from '../lib/picar/use-car-link.js';

/**
 * Finds the CarLink of the loaded picar extension and renders the monitor for it.
 * @param {object} props - props
 * @param {VM} props.vm - the scratch-vm instance
 * @returns {?React.Element} the monitor, or null while the extension is not loaded
 */
const CarMonitor = ({vm}) => {
    const link = useCarLink(vm);
    if (!link) return null;
    return (
        <CarMonitorComponent
            link={link}
            vm={vm}
        />
    );
};

CarMonitor.propTypes = {
    vm: PropTypes.instanceOf(VM).isRequired
};

const mapStateToProps = state => ({
    vm: state.scratchGui.vm
});

export {CarMonitor as Unconnected};
export default connect(mapStateToProps)(CarMonitor);
