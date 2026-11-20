import {useCallback} from 'react';
import {useIntl} from 'react-intl';
import {useStore} from 'react-redux';

import log from '../log.js';
import sharedMessages from '../shared-messages';
import {
    getIsShowingWithoutId,
    onLoadedProject,
    requestProjectUpload
} from '../../reducers/project-state.js';
import {closeLoadingProject, openLoadingProject} from '../../reducers/modals.js';
import {setProjectTitle} from '../../reducers/project-title.js';

/**
 * Load an sb3 held in memory into the editor. Mirrors the steps of sb-file-uploader-hoc.jsx
 * (confirm if the project changed, request the upload state, show the loading screen,
 * vm.loadProject, set the title, finish loading, close the loading screen).
 * @param {object} options - options
 * @param {ArrayBuffer} options.buffer - the project file
 * @param {string} options.title - project title to show afterwards
 * @param {object} options.vm - the scratch-vm instance
 * @param {Function} options.dispatch - redux dispatch
 * @param {Function} options.getState - redux getState
 * @param {string} options.confirmMessage - text of the "replace the current project?" confirmation
 * @param {boolean} [options.canSave] - passed to onLoadedProject
 * @returns {Promise<boolean>} false when the user declined or the editor is busy, true when loaded;
 *   rejects when the project could not be loaded
 */
const loadProjectBuffer = async ({buffer, title, vm, dispatch, getState, confirmMessage, canSave = false}) => {
    const gui = getState().scratchGui;
    const loadingState = gui.projectState.loadingState;
    if (gui.projectChanged && getIsShowingWithoutId(loadingState)) {
        const allowed = confirm(confirmMessage); // eslint-disable-line no-alert
        if (!allowed) return false;
    }
    const request = requestProjectUpload(loadingState);
    if (!request) return false; // already loading or saving
    dispatch(request);
    dispatch(openLoadingProject());

    let success = false;
    let failure = null;
    try {
        await vm.loadProject(buffer);
        dispatch(setProjectTitle(title));
        success = true;
    } catch (error) {
        log.warn(error);
        failure = error;
    }
    const finished = onLoadedProject(getState().scratchGui.projectState.loadingState, canSave, success);
    if (finished) dispatch(finished);
    dispatch(closeLoadingProject());
    if (failure) throw failure;
    return true;
};

/**
 * @returns {Function} (buffer, title) => Promise<boolean>, see loadProjectBuffer
 */
const useLoadProjectBuffer = () => {
    const store = useStore();
    const intl = useIntl();
    return useCallback((buffer, title) => loadProjectBuffer({
        buffer,
        title,
        vm: store.getState().scratchGui.vm,
        dispatch: store.dispatch,
        getState: store.getState,
        confirmMessage: intl.formatMessage(sharedMessages.replaceProjectWarning)
    }), [store, intl]);
};

export {loadProjectBuffer, useLoadProjectBuffer};
