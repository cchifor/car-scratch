/**
 * @returns {Promise<Array<{id: string, title: string, description: string, level: ?number, needs: ?Array,
 *   world: ?string}>>} bundled examples; an empty list when the index is missing or invalid
 */
const fetchExamples = async () => {
    try {
        const response = await fetch('examples/index.json');
        if (!response.ok) return [];
        const data = await response.json();
        const list = Array.isArray(data) ? data : (data && data.examples);
        return Array.isArray(list) ? list.filter(item => item && typeof item.id === 'string') : [];
    } catch (e) {
        return [];
    }
};

/**
 * @param {string} id - example id
 * @returns {Promise<ArrayBuffer>} the example project file
 */
const fetchExample = async id => {
    const response = await fetch(`examples/${encodeURIComponent(id)}.sb3`);
    if (!response.ok) throw new Error(`Could not load the example (${response.status})`);
    return response.arrayBuffer();
};

export {fetchExample, fetchExamples};
