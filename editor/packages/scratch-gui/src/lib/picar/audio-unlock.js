import {useEffect} from 'react';

/**
 * Browsers (iPad Safari in particular) keep the AudioContext suspended until a user gesture.
 * Resume the VM's shared context on the first pointerdown and ask for the "playback" audio session
 * so sounds play with the silent switch on. Best effort: failures are ignored.
 * @param {?object} vm - the scratch-vm instance
 * @returns {void}
 */
const useAudioUnlock = vm => {
    useEffect(() => {
        const unlock = () => {
            try {
                const engine = vm && vm.runtime && vm.runtime.audioEngine;
                const context = engine && engine.audioContext;
                if (context && context.state === 'suspended') {
                    const result = context.resume();
                    if (result && result.catch) result.catch(() => {});
                }
                if (typeof navigator !== 'undefined' && navigator.audioSession) {
                    navigator.audioSession.type = 'playback';
                }
            } catch (e) {
                // Best effort only.
            }
        };
        document.addEventListener('pointerdown', unlock, {once: true, capture: true});
        return () => document.removeEventListener('pointerdown', unlock, {capture: true});
    }, [vm]);
};

export {useAudioUnlock};
