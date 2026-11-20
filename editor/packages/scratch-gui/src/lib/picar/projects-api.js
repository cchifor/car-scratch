// REST helpers for projects stored on the car (docs/protocol.md section 6).

const PROJECT_NAME_PATTERN = /^[A-Za-z0-9 _-]{1,64}$/;
const SB3_CONTENT_TYPE = 'application/x.scratch.sb3';

/**
 * @param {string} name - project name without the .sb3 extension
 * @returns {boolean} whether the server accepts the name
 */
const isValidProjectName = name => PROJECT_NAME_PATTERN.test(name);

/**
 * @param {string} name - project name without the .sb3 extension
 * @returns {string} URL of the project file (relative to the page)
 */
const projectUrl = name => `api/projects/${encodeURIComponent(name)}.sb3`;

const failure = async (response, fallback) => {
    let detail = '';
    try {
        detail = (await response.text()).slice(0, 200);
    } catch (e) {
        detail = '';
    }
    return new Error(`${fallback} (${response.status}${detail ? `: ${detail}` : ''})`);
};

/**
 * @returns {Promise<Array<{name: string, size: number, mtime: number}>>} the projects stored on the car
 */
const listProjects = async () => {
    const response = await fetch('api/projects');
    if (!response.ok) throw await failure(response, 'Could not list projects');
    const list = await response.json();
    return Array.isArray(list) ? list : [];
};

/**
 * @param {string} name - project name
 * @returns {Promise<ArrayBuffer>} the project file
 */
const fetchProject = async name => {
    const response = await fetch(projectUrl(name));
    if (!response.ok) throw await failure(response, 'Could not open the project');
    return response.arrayBuffer();
};

/**
 * @param {string} name - project name (validated by the caller)
 * @param {ArrayBuffer|Uint8Array|Blob} data - sb3 bytes
 * @returns {Promise<void>} resolves when the car has stored the project
 */
const saveProject = async (name, data) => {
    const response = await fetch(projectUrl(name), {
        method: 'PUT',
        headers: {'Content-Type': SB3_CONTENT_TYPE},
        body: data
    });
    if (!response.ok) throw await failure(response, 'Could not save the project');
};

export {
    PROJECT_NAME_PATTERN,
    SB3_CONTENT_TYPE,
    fetchProject,
    isValidProjectName,
    listProjects,
    projectUrl,
    saveProject
};
