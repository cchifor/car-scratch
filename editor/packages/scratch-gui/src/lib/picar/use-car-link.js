import {useEffect, useState} from 'react';

/**
 * @param {?object} vm - the scratch-vm instance
 * @returns {?object} the CarLink exposed by the picar extension, or null while it is not available
 */
const findCarLink = vm => {
    const ext = vm && vm.runtime && vm.runtime.peripheralExtensions && vm.runtime.peripheralExtensions.picar;
    return (ext && ext.link) || null;
};

/**
 * React hook returning the CarLink (docs/contracts.md section 7). The extension registers itself
 * when it is loaded, so the lookup is repeated whenever the VM reports an added extension or a
 * loaded project.
 * @param {?object} vm - the scratch-vm instance
 * @returns {?object} the link, or null
 */
const useCarLink = vm => {
    const [link, setLink] = useState(() => findCarLink(vm));
    useEffect(() => {
        if (!vm) return;
        const update = () => setLink(current => {
            const found = findCarLink(vm);
            return found === current ? current : found;
        });
        update();
        vm.on('EXTENSION_ADDED', update);
        vm.on('workspaceUpdate', update);
        return () => {
            vm.removeListener('EXTENSION_ADDED', update);
            vm.removeListener('workspaceUpdate', update);
        };
    }, [vm]);
    return link;
};

/**
 * React hook returning the link's coarse state snapshot, re-rendering only when the link reports a change.
 * @param {?object} link - the CarLink
 * @returns {?object} link.getState(), or null without a link
 */
const useCarState = link => {
    const [state, setState] = useState(() => (link ? link.getState() : null));
    useEffect(() => {
        if (!link) {
            setState(null);
            return;
        }
        setState(link.getState());
        return link.subscribe(() => setState(link.getState()));
    }, [link]);
    return state;
};

export {findCarLink, useCarLink, useCarState};
