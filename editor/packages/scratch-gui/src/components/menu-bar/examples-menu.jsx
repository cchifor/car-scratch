import React, {useCallback, useEffect, useRef, useState} from 'react';
import classNames from 'classnames';
import PropTypes from 'prop-types';
import {connect} from 'react-redux';
import {defineMessages, FormattedMessage, useIntl} from 'react-intl';

import MenuBarMenu from './menu-bar-menu.jsx';
import {MenuItem} from '../menu/menu.jsx';
import useMenuNavigation from '../../hooks/use-menu-navigation';
import dropdownCaret from './dropdown-caret.svg';
import {fetchExample, fetchExamples} from '../../lib/picar/examples-api.js';
import {useLoadProjectBuffer} from '../../lib/picar/load-project-buffer.js';
import {findCarLink} from '../../lib/picar/use-car-link.js';

import styles from './menu-bar.css';

const messages = defineMessages({
    aria: {
        id: 'picar.examples.aria',
        defaultMessage: 'Examples menu',
        description: 'Accessibility label of the Examples menu'
    },
    title: {
        id: 'picar.examples.title',
        defaultMessage: 'Examples',
        description: 'Menu bar: example projects'
    },
    none: {
        id: 'picar.examples.none',
        defaultMessage: 'No examples',
        description: 'Shown in the Examples menu when the car has no example projects'
    },
    loadError: {
        id: 'picar.examples.loadError',
        defaultMessage: 'The example failed to load.',
        description: 'Alert when an example project cannot be loaded'
    }
});

/**
 * Menu of the example projects bundled on the car (examples/index.json).
 * @param {object} props - props
 * @param {boolean} [props.isRtl] - right-to-left layout
 * @param {object} [props.vm] - the scratch-vm instance (used to find the car link)
 * @param {number} [props.depth] - menu nesting depth
 * @returns {React.Element} the menu
 */
const ExamplesMenu = ({isRtl, vm, depth}) => {
    const intl = useIntl();
    const loadBuffer = useLoadProjectBuffer();
    const [examples, setExamples] = useState(null);

    const {
        menuRef,
        isExpanded,
        handleKeyDown,
        handleKeyDownOpenMenu,
        handleOnOpen,
        handleOnClose
    } = useMenuNavigation({depth, isRtl});

    const expanded = isExpanded();

    // The menu closes itself on any pointerup outside it, and the trigger button is outside it, so by
    // the time the button's click fires the menu is already closed and a plain "open" handler would
    // reopen it. The state at pointerdown decides whether the click opens or closes. A click with no
    // preceding pointerdown (keyboard activation) falls back to the current state.
    const openAtPointerDown = useRef(null);
    const handlePointerDown = useCallback(() => {
        openAtPointerDown.current = expanded;
    }, [expanded]);
    // Clicks on the menu items bubble to the button, so choosing an item closes the menu too.
    const handleClick = useCallback(() => {
        const wasOpen = openAtPointerDown.current ?? expanded;
        openAtPointerDown.current = null;
        if (wasOpen) {
            handleOnClose();
        } else {
            handleOnOpen();
        }
    }, [expanded, handleOnClose, handleOnOpen]);
    // The index is fetched when the menu is first opened, and again on later opens so new
    // examples on the car show up without reloading the editor.
    useEffect(() => {
        if (!expanded) return;
        let cancelled = false;
        fetchExamples().then(list => {
            if (!cancelled) setExamples(list);
        });
        return () => {
            cancelled = true;
        };
    }, [expanded]);

    const choose = useCallback(example => async () => {
        try {
            const buffer = await fetchExample(example.id);
            const loaded = await loadBuffer(buffer, example.title || example.id);
            const link = findCarLink(vm);
            if (loaded && example.world && link && link.getState().mode === 'sim') {
                link.send('world', {world: example.world});
            }
        } catch (e) {
            alert(intl.formatMessage(messages.loadError)); // eslint-disable-line no-alert
        }
    }, [loadBuffer, vm, intl]);

    return (
        <button
            className={classNames(styles.menuBarItem, styles.hoverable, {
                [styles.active]: expanded
            })}
            onClick={handleClick}
            onPointerDown={handlePointerDown}
            aria-label={intl.formatMessage(messages.aria)}
            aria-expanded={expanded}
            onKeyDown={handleKeyDown}
            ref={menuRef}
        >
            <span className={styles.examplesLabel}>
                <FormattedMessage {...messages.title} />
            </span>
            <img src={dropdownCaret} />
            <MenuBarMenu
                className={classNames(styles.menuBarMenu)}
                open={expanded}
                place={isRtl ? 'left' : 'right'}
                onRequestClose={handleOnClose}
            >
                {examples && examples.length > 0 ? examples.map(example => (
                    <MenuItem
                        key={example.id}
                        onClick={choose(example)}
                        isDataMenuItem
                        onParentKeyDown={handleKeyDownOpenMenu}
                    >
                        <span title={example.description || ''}>{example.title || example.id}</span>
                    </MenuItem>
                )) : (
                    <MenuItem
                        isDisabled
                        isDataMenuItem
                        onParentKeyDown={handleKeyDownOpenMenu}
                    >
                        <FormattedMessage {...messages.none} />
                    </MenuItem>
                )}
            </MenuBarMenu>
        </button>
    );
};

ExamplesMenu.propTypes = {
    depth: PropTypes.number,
    isRtl: PropTypes.bool,
    vm: PropTypes.shape({})
};

const mapStateToProps = state => ({
    isRtl: state.locales.isRtl,
    vm: state.scratchGui.vm
});

export {ExamplesMenu as Unconnected};
export default connect(mapStateToProps)(ExamplesMenu);
