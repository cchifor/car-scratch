// Scratch's own mascot and character sprites and costumes do not belong in the Car Scratch libraries.
const SCRATCH_CHARACTER = /^(cat|gobo|pico|nano|tera|giga)/i;

/**
 * @param {Array<{name: string}>} items - a sprite or costume library
 * @returns {Array<{name: string}>} the library without the Scratch characters
 */
const withoutScratchCharacters = items => items.filter(item => !SCRATCH_CHARACTER.test(String(item && item.name)));

export {SCRATCH_CHARACTER, withoutScratchCharacters};
