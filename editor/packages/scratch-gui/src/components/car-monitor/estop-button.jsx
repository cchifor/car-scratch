import classNames from 'classnames';
import PropTypes from 'prop-types';
import React, {useCallback} from 'react';
import {FormattedMessage, useIntl} from 'react-intl';

import messages from './messages.js';
import styles from './car-monitor.css';

/**
 * Emergency stop. Fires on pointerdown (not click) so the car stops as early as possible;
 * keyboard activation arrives as a click with detail 0.
 * @param {object} props - props
 * @param {?object} props.link - the CarLink
 * @param {string} [props.className] - extra class
 * @returns {React.Element} the button
 */
const EStopButton = ({link, className}) => {
    const intl = useIntl();
    const fire = useCallback(() => {
        if (link) link.estop();
    }, [link]);
    const onPointerDown = useCallback(event => {
        // Only primary buttons / touches count; a context-menu click must not stop the car by surprise either way.
        if (event.button > 0) return;
        fire();
    }, [fire]);
    const onClick = useCallback(event => {
        if (event.detail === 0) fire();
    }, [fire]);
    return (
        <button
            type="button"
            className={classNames(styles.estop, className)}
            aria-label={intl.formatMessage(messages.estopAria)}
            onPointerDown={onPointerDown}
            onClick={onClick}
        >
            <FormattedMessage {...messages.estop} />
        </button>
    );
};

EStopButton.propTypes = {
    className: PropTypes.string,
    link: PropTypes.shape({estop: PropTypes.func})
};

export default EStopButton;
