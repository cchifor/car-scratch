import PropTypes from 'prop-types';
import React from 'react';
import {FormattedMessage, useIntl} from 'react-intl';

import Modal from '../modal/modal.jsx';
import messages from './messages.js';
import styles from './car-info-modal.css';

/**
 * Text dialogs of the About menu.
 * @param {object} props - props
 * @param {string} props.kind - 'about' or 'safety'
 * @param {?string} props.version - car software version, when connected
 * @param {Function} props.onClose - close the dialog
 * @returns {React.Element} the dialog
 */
const CarInfoModal = ({kind, version, onClose}) => {
    const intl = useIntl();
    const isAbout = kind === 'about';
    return (
        <Modal
            className={styles.modalContent}
            contentLabel={intl.formatMessage(isAbout ? messages.aboutTitle : messages.safetyTitle)}
            onRequestClose={onClose}
        >
            <div className={styles.body}>
                {isAbout ? (
                    <React.Fragment>
                        <p>
                            <FormattedMessage
                                {...messages.version}
                                values={{version: version || intl.formatMessage(messages.versionUnknown)}}
                            />
                        </p>
                        <p><FormattedMessage {...messages.notAffiliated} /></p>
                        <p><FormattedMessage {...messages.license} /></p>
                    </React.Fragment>
                ) : (
                    <React.Fragment>
                        <p><FormattedMessage {...messages.safety1} /></p>
                        <p><FormattedMessage {...messages.safety2} /></p>
                        <p><FormattedMessage {...messages.safety3} /></p>
                        <p><FormattedMessage {...messages.safety4} /></p>
                    </React.Fragment>
                )}
                <button
                    type="button"
                    className={styles.close}
                    onClick={onClose}
                >
                    <FormattedMessage {...messages.close} />
                </button>
            </div>
        </Modal>
    );
};

CarInfoModal.propTypes = {
    kind: PropTypes.oneOf(['about', 'safety']).isRequired,
    onClose: PropTypes.func.isRequired,
    version: PropTypes.string
};

export default CarInfoModal;
