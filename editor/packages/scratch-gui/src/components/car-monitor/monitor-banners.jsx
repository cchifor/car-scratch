import classNames from 'classnames';
import PropTypes from 'prop-types';
import React from 'react';
import {FormattedMessage} from 'react-intl';

import messages from './messages.js';
import styles from './car-monitor.css';

const TEXT = {
    obstacle: messages.bannerObstacle,
    estop: messages.bannerEstop,
    hwfault: messages.bannerHwFault,
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
    hwReset: messages.bannerEstopReset,
    resume: messages.bannerResume,
    take: messages.bannerTakeControl,
    reload: messages.bannerReloadButton
};

const TONE_CLASS = {
    bad: styles.bannerBad,
    warn: styles.bannerWarn,
    info: styles.bannerInfo
};

const Banner = ({banner, onAction, className}) => (
    <div
        data-banner={banner.id}
        className={classNames(
            styles.banner,
            TONE_CLASS[banner.tone],
            {[styles.bannerSmall]: banner.small},
            className
        )}
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
);

Banner.propTypes = {
    banner: PropTypes.shape({
        id: PropTypes.string.isRequired,
        tone: PropTypes.string,
        small: PropTypes.bool,
        action: PropTypes.string
    }).isRequired,
    className: PropTypes.string,
    onAction: PropTypes.func.isRequired
};

/**
 * Persistent banners: a normal flex child between the header and the canvas (never over the world).
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
        {banners.filter(banner => banner.id !== 'obstacle').map(banner => (
            <Banner
                key={banner.id}
                banner={banner}
                onAction={onAction}
            />
        ))}
    </div>
);

/**
 * The transient OBSTACLE STOP banner: a small overlay pinned to the canvas top-right corner.
 * @param {object} props - props
 * @param {Array<{id: string}>} props.banners - see computeBanners
 * @returns {?React.Element} the overlay, or null
 */
const ObstacleOverlay = ({banners}) => {
    const banner = banners.find(b => b.id === 'obstacle');
    if (!banner) return null;
    return (
        <div role="alert">
            <Banner
                banner={Object.assign({}, banner, {small: true})}
                className={styles.bannerOverlay}
                onAction={() => {}} // eslint-disable-line react/jsx-no-bind
            />
        </div>
    );
};

ObstacleOverlay.propTypes = {
    banners: PropTypes.arrayOf(PropTypes.shape({id: PropTypes.string})).isRequired
};

MonitorBanners.propTypes = {
    banners: PropTypes.arrayOf(PropTypes.shape({
        id: PropTypes.string.isRequired,
        tone: PropTypes.string,
        small: PropTypes.bool,
        action: PropTypes.string
    })).isRequired,
    onAction: PropTypes.func.isRequired
};

export {MonitorBanners as default, ObstacleOverlay};
