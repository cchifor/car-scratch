import PropTypes from 'prop-types';
import React, {useCallback} from 'react';
import {useIntl} from 'react-intl';
import {connect} from 'react-redux';

import Modal from '../modal/modal.jsx';
import {fetchProject} from '../../lib/picar/projects-api.js';
import {useLoadProjectBuffer} from '../../lib/picar/load-project-buffer.js';
import {setProjectTitle} from '../../reducers/project-title';
import {closeCarProjectsOpen, closeCarProjectsSave} from '../../reducers/modals';
import {OpenPanel, SavePanel} from './car-projects-panels.jsx';
import messages from './messages.js';
import styles from './car-projects-modal.css';

const suggestName = title => (title || '').replace(/[^A-Za-z0-9 _-]/g, '').trim()
    .slice(0, 64);

const CarProjectsModal = ({
    isRtl, openVisible, saveVisible, projectTitle, vm, onCloseOpen, onCloseSave, onSaved
}) => {
    const intl = useIntl();
    const loadBuffer = useLoadProjectBuffer();
    const onOpenProject = useCallback(async name => {
        const buffer = await fetchProject(name);
        await loadBuffer(buffer, name);
    }, [loadBuffer]);
    const saveSb3 = useCallback(() => vm.saveProjectSb3(), [vm]);
    if (openVisible) {
        return (
            <Modal
                className={styles.modalContent}
                contentLabel={intl.formatMessage(messages.openTitle)}
                isRtl={isRtl}
                onRequestClose={onCloseOpen}
            >
                <OpenPanel
                    onClose={onCloseOpen}
                    onOpenProject={onOpenProject}
                />
            </Modal>
        );
    }
    if (saveVisible) {
        return (
            <Modal
                className={styles.modalContent}
                contentLabel={intl.formatMessage(messages.saveTitle)}
                isRtl={isRtl}
                onRequestClose={onCloseSave}
            >
                <SavePanel
                    initialName={suggestName(projectTitle)}
                    saveSb3={saveSb3}
                    onSaved={onSaved}
                    onClose={onCloseSave}
                />
            </Modal>
        );
    }
    return null;
};

CarProjectsModal.propTypes = {
    isRtl: PropTypes.bool,
    onCloseOpen: PropTypes.func,
    onCloseSave: PropTypes.func,
    onSaved: PropTypes.func,
    openVisible: PropTypes.bool,
    projectTitle: PropTypes.string,
    saveVisible: PropTypes.bool,
    vm: PropTypes.shape({saveProjectSb3: PropTypes.func})
};

const mapStateToProps = state => ({
    isRtl: state.locales.isRtl,
    openVisible: state.scratchGui.modals.carProjectsOpen,
    saveVisible: state.scratchGui.modals.carProjectsSave,
    projectTitle: state.scratchGui.projectTitle,
    vm: state.scratchGui.vm
});

const mapDispatchToProps = dispatch => ({
    onCloseOpen: () => dispatch(closeCarProjectsOpen()),
    onCloseSave: () => dispatch(closeCarProjectsSave()),
    onSaved: name => dispatch(setProjectTitle(name))
});

export default connect(mapStateToProps, mapDispatchToProps)(CarProjectsModal);
