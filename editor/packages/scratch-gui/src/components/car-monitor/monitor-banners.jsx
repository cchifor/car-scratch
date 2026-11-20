import classNames from 'classnames';
import PropTypes from 'prop-types';
import React from 'react';
import {FormattedMessage} from 'react-intl';

import messages from './messages.js';
import styles from './car-monitor.css';

const TEXT = {
    obstacle: messages.bannerObstacle,
    estop: messages.bannerEstop,
    paused: messages.bannerPaused,
    viewOnly: messages.bannerViewOnly,
    notCalibrated: messages.bannerNotCalibrated,
    obstacleStopOff: messages.bannerObstacleStopOff,
    lineOffline: messages.bannerLineOffline,
    noCar: messages.bannerNoCar,
    reconnecting: messages.bannerReconnecting,
    reload: messages.bannerReload
};

const ACTION_TEXT = {
    estopReset: messages.bannerEstopReset,
    resume: messages.bannerResume,
    take: messages.bannerTakeControl,
    reload: messages.bannerReloadButton
};

const TONE_CLASS = {
    bad: styles.bannerBad,
    warn: styles.bannerWarn,
    info: styles.bannerInfo
};

/**
 * @param {object} props - props
 * @param {Array<{id: string, tone: string, small: boolean, action: ?string}>} props.banners - see computeBanners
 * @param {Function} props.onAction - called with the action id of a pressed banner button
 * @returns {React.Element} the banner stack
 */
const MonitorBanners = ({banners, onAction}) => (
    <div
        className={styles.banners}
        role="alert"
    >
        {banners.map(banner => (
            <div
                key={banner.id}
                data-banner={banner.id}
                className={classNames(styles.banner, TONE_CLASS[banner.tone], {[styles.bannerSmall]: banner.small})}
            >
                <span className={styles.bannerText}>
                    <FormattedMessage {...TEXT[banner.id]} />
                </span>
                {banner.action ? (
                    <button
                        type="button"
                        className={styles.bannerButton}
                        onClick={() => onAction(banner.action)} // eslint-disable-line react/jsx-no-bind
                    >
                        <FormattedMessage {...ACTION_TEXT[banner.action]} />
                    </button>
                ) : null}
            </div>
        ))}
    </div>
);

MonitorBanners.propTypes = {
    banners: PropTypes.arrayOf(PropTypes.shape({
        id: PropTypes.string.isRequired,
        tone: PropTypes.string,
        small: PropTypes.bool,
        action: PropTypes.string
    })).isRequired,
    onAction: PropTypes.func.isRequired
};

export default MonitorBanners;
