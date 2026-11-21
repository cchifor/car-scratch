/**
 * localStorage access that never throws (private windows, blocked site data).
 * @param {string} key - storage key
 * @returns {?string} the stored value, or null
 */
const readSetting = key => {
    try {
        return window.localStorage.getItem(key);
    } catch (e) {
        return null;
    }
};

/**
 * @param {string} key - storage key
 * @param {string} value - value to store
 * @returns {void}
 */
const writeSetting = (key, value) => {
    try {
        window.localStorage.setItem(key, value);
    } catch (e) {
        // Storage is only a convenience; ignore failures.
    }
};

export {readSetting, writeSetting};
