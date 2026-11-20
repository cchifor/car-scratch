import PropTypes from 'prop-types';
import React, {useCallback, useEffect, useState} from 'react';

import CarInfoModal from '../components/car-info-modal/car-info-modal.jsx';
import RotateHint from '../components/car-monitor/rotate-hint.jsx';
import {EVENT_NAME} from '../lib/picar/about-events.js';
import {useAudioUnlock} from '../lib/picar/audio-unlock.js';
import {useCarLink, useCarState} from '../lib/picar/use-car-link.js';

/**
 * Page-wide behaviour for the car editor: the portrait "rotate" hint, the audio unlock on the
 * first tap, and the About / Safety dialogs requested from the menu bar.
 * @param {object} props - props
 * @param {object} props.vm - the scratch-vm instance
 * @returns {React.Element} overlays
 */
const PicarGlobal = ({vm}) => {
    useAudioUnlock(vm);
    const state = useCarState(useCarLink(vm));
    const [dialog, setDialog] = useState(null);
    useEffect(() => {
        const onRequest = event => setDialog(event.detail);
        window.addEventListener(EVENT_NAME, onRequest);
        return () => window.removeEventListener(EVENT_NAME, onRequest);
    }, []);
    const close = useCallback(() => setDialog(null), []);
    return (
        <React.Fragment>
            <RotateHint />
            {dialog ? (
                <CarInfoModal
                    kind={dialog}
                    version={state && state.server ? state.server.version : null}
                    onClose={close}
                />
            ) : null}
        </React.Fragment>
    );
};

PicarGlobal.propTypes = {
    vm: PropTypes.shape({})
};

export default PicarGlobal;
