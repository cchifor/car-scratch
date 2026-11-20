import classNames from 'classnames';
import PropTypes from 'prop-types';
import React, {useCallback, useState} from 'react';
import {FormattedMessage, useIntl} from 'react-intl';

import messages from '../car-monitor/messages.js';
import {readSetting, writeSetting} from '../../lib/picar/storage.js';

import styles from './stage-pane-tabs.css';

const STORAGE_KEY = 'picar.stagePaneTab';
const TAB_SPRITES = 'sprites';
const TAB_MONITOR = 'monitor';

const isTouchDevice = () => {
    try {
        return Boolean(window.matchMedia && window.matchMedia('(any-pointer: coarse)').matches);
    } catch (e) {
        return false;
    }
};

// The stored choice wins; with none, touch devices (iPad) start on the Pi Car monitor and
// desktops on Sprites (the sprite list selects whose code you edit).
const initialTab = () => {
    const stored = readSetting(STORAGE_KEY);
    if (stored === TAB_MONITOR || stored === TAB_SPRITES) return stored;
    return isTouchDevice() ? TAB_MONITOR : TAB_SPRITES;
};

/**
 * The bottom-right pane: "Sprites | Pi Car". Both panels stay mounted and the inactive one is
 * hidden with CSS, so the sprite selector keeps its scroll position and state and the monitor
 * keeps its trail. The choice is remembered in localStorage.
 * @param {object} props - props
 * @param {React.Node} props.sprites - the sprite / stage selector pane
 * @param {React.Node} props.monitor - the car monitor
 * @returns {React.Element} the tabbed pane
 */
const StagePaneTabs = ({sprites, monitor}) => {
    const intl = useIntl();
    const [tab, setTab] = useState(initialTab);
    const select = useCallback(next => () => {
        setTab(next);
        writeSetting(STORAGE_KEY, next);
    }, []);
    const tabButton = (id, message) => (
        <button
            type="button"
            role="tab"
            id={`picar-tab-${id}`}
            aria-selected={tab === id}
            aria-controls={`picar-panel-${id}`}
            className={classNames(styles.tab, {[styles.selected]: tab === id})}
            onClick={select(id)}
        >
            <FormattedMessage {...message} />
        </button>
    );
    return (
        <div className={styles.tabs}>
            <div
                className={styles.tabList}
                role="tablist"
                aria-label={intl.formatMessage(messages.tabsAria)}
            >
                {tabButton(TAB_SPRITES, messages.tabSprites)}
                {tabButton(TAB_MONITOR, messages.tabMonitor)}
            </div>
            <div
                id={`picar-panel-${TAB_SPRITES}`}
                role="tabpanel"
                aria-labelledby={`picar-tab-${TAB_SPRITES}`}
                className={classNames(styles.panel, {[styles.hidden]: tab !== TAB_SPRITES})}
            >
                {sprites}
            </div>
            <div
                id={`picar-panel-${TAB_MONITOR}`}
                role="tabpanel"
                aria-labelledby={`picar-tab-${TAB_MONITOR}`}
                className={classNames(styles.panel, {[styles.hidden]: tab !== TAB_MONITOR})}
            >
                {monitor}
            </div>
        </div>
    );
};

StagePaneTabs.propTypes = {
    monitor: PropTypes.node,
    sprites: PropTypes.node
};

export default StagePaneTabs;
