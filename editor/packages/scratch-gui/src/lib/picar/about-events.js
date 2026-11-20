const EVENT_NAME = 'picar-info';

/**
 * The About menu items are created outside the redux/intl tree (render-gui); they ask for a dialog
 * with a window event that PicarGlobal listens for.
 * @param {string} kind - 'about' or 'safety'
 * @returns {void}
 */
const requestInfoDialog = kind => {
    window.dispatchEvent(new CustomEvent(EVENT_NAME, {detail: kind}));
};

export {EVENT_NAME, requestInfoDialog};
