import PropTypes from 'prop-types';
import React, {useEffect, useState} from 'react';
import {FormattedMessage} from 'react-intl';

import EStopButton from './estop-button.jsx';
import messages from './messages.js';
import styles from './rotate-hint.css';

const QUERY = '(orientation: portrait) and (pointer: coarse)';

const matches = () => typeof window !== 'undefined' && typeof window.matchMedia === 'function' &&
    window.matchMedia(QUERY).matches;

/**
 * Full-page overlay asking a touch device in portrait to rotate to landscape. Dismissible.
 * @param {object} props - props
 * @param {boolean} [props.forceVisible] - show regardless of orientation (tests)
 * @param {?object} [props.link] - the CarLink: the overlay covers both E-STOPs, so it carries its own
 * @returns {?React.Element} the overlay, or null
 */
const RotateHint = ({forceVisible, link}) => {
    const [portrait, setPortrait] = useState(matches);
    const [dismissed, setDismissed] = useState(false);
    useEffect(() => {
        if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
        const mql = window.matchMedia(QUERY);
        const onChange = () => setPortrait(mql.matches);
        onChange();
        if (mql.addEventListener) {
            mql.addEventListener('change', onChange);
            return () => mql.removeEventListener('change', onChange);
        }
        mql.addListener(onChange);
        return () => mql.removeListener(onChange);
    }, []);
    if (dismissed || !(portrait || forceVisible)) return null;
    return (
        <div
            className={styles.overlay}
            role="alertdialog"
            aria-labelledby="picar-rotate-text"
        >
            <div className={styles.phone} />
            <p
                id="picar-rotate-text"
                className={styles.text}
            >
                <FormattedMessage {...messages.rotateHint} />
            </p>
            <button
                type="button"
                className={styles.dismiss}
                onClick={() => setDismissed(true)} // eslint-disable-line react/jsx-no-bind
            >
                <FormattedMessage {...messages.rotateDismiss} />
            </button>
            {link ? (
                <EStopButton
                    link={link}
                    className={styles.estop}
                />
            ) : null}
        </div>
    );
};

RotateHint.propTypes = {
    forceVisible: PropTypes.bool,
    link: PropTypes.shape({estop: PropTypes.func})
};

export default RotateHint;
