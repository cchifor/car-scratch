import PropTypes from 'prop-types';
import React, {useCallback, useEffect, useState} from 'react';
import {FormattedMessage} from 'react-intl';

import {isValidProjectName, listProjects, saveProject} from '../../lib/picar/projects-api.js';
import messages from './messages.js';
import styles from './car-projects-modal.css';

const errorText = e => (e && e.message) || String(e);

const ErrorLine = ({detail}) => (detail ? (
    <p
        className={styles.error}
        role="alert"
    >
        <FormattedMessage
            {...messages.error}
            values={{detail}}
        />
    </p>
) : null);

ErrorLine.propTypes = {detail: PropTypes.string};

/**
 * Lists the projects on the car; choosing one opens it.
 * @param {object} props - props
 * @param {Function} props.onOpenProject - (name) => Promise; rejects with an Error to show inline
 * @param {Function} props.onClose - close the dialog
 * @returns {React.Element} the panel
 */
const OpenPanel = ({onOpenProject, onClose}) => {
    const [projects, setProjects] = useState(null);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    useEffect(() => {
        let cancelled = false;
        listProjects()
            .then(list => {
                if (!cancelled) setProjects(list);
            })
            .catch(e => {
                if (cancelled) return;
                setError(errorText(e));
                setProjects([]);
            });
        return () => {
            cancelled = true;
        };
    }, []);
    const open = useCallback(name => async () => {
        setBusy(true);
        setError('');
        try {
            await onOpenProject(name);
            onClose();
        } catch (e) {
            setError(errorText(e));
            setBusy(false);
        }
    }, [onOpenProject, onClose]);
    return (
        <div className={styles.body}>
            <ErrorLine detail={error} />
            {projects === null ? <p><FormattedMessage {...messages.loading} /></p> : null}
            {projects && projects.length === 0 && !error ? <p><FormattedMessage {...messages.empty} /></p> : null}
            <ul className={styles.list}>
                {(projects || []).map(p => (
                    <li key={p.name}>
                        <button
                            type="button"
                            className={styles.listItem}
                            disabled={busy}
                            onClick={open(p.name)}
                        >
                            <span className={styles.itemName}>{p.name}</span>
                            <span className={styles.itemMeta}>
                                {p.mtime ? new Date(p.mtime * 1000).toLocaleString() : ''}
                            </span>
                        </button>
                    </li>
                ))}
            </ul>
            <div className={styles.buttons}>
                <button
                    type="button"
                    className={styles.button}
                    onClick={onClose}
                >
                    <FormattedMessage {...messages.cancel} />
                </button>
            </div>
        </div>
    );
};

OpenPanel.propTypes = {
    onClose: PropTypes.func.isRequired,
    onOpenProject: PropTypes.func.isRequired
};

/**
 * Name field + Save button that stores the current project on the car.
 * @param {object} props - props
 * @param {string} props.initialName - suggested name
 * @param {Function} props.saveSb3 - () => Promise<Blob> producing the project file
 * @param {Function} props.onSaved - called with the name after a successful save
 * @param {Function} props.onClose - close the dialog
 * @returns {React.Element} the panel
 */
const SavePanel = ({initialName, saveSb3, onSaved, onClose}) => {
    const [name, setName] = useState(initialName);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const valid = isValidProjectName(name);
    const submit = useCallback(async event => {
        if (event) event.preventDefault();
        if (!valid || busy) return;
        setBusy(true);
        setError('');
        try {
            const blob = await saveSb3();
            await saveProject(name, blob);
            onSaved(name);
            onClose();
        } catch (e) {
            setError(errorText(e));
            setBusy(false);
        }
    }, [valid, busy, name, saveSb3, onSaved, onClose]);
    const onChange = useCallback(e => setName(e.target.value), []);
    return (
        <form
            className={styles.body}
            onSubmit={submit}
        >
            <label className={styles.label}>
                <FormattedMessage {...messages.nameLabel} />
                <input
                    className={styles.input}
                    type="text"
                    value={name}
                    maxLength={64}
                    autoFocus
                    onChange={onChange}
                />
            </label>
            {valid ? null : (
                <p
                    className={styles.hint}
                    data-testid="name-invalid"
                >
                    <FormattedMessage {...messages.nameInvalid} />
                </p>
            )}
            <ErrorLine detail={error} />
            <div className={styles.buttons}>
                <button
                    type="button"
                    className={styles.button}
                    onClick={onClose}
                >
                    <FormattedMessage {...messages.cancel} />
                </button>
                <button
                    type="submit"
                    className={styles.buttonPrimary}
                    disabled={!valid || busy}
                >
                    <FormattedMessage {...(busy ? messages.saving : messages.save)} />
                </button>
            </div>
        </form>
    );
};

SavePanel.propTypes = {
    initialName: PropTypes.string,
    onClose: PropTypes.func.isRequired,
    onSaved: PropTypes.func,
    saveSb3: PropTypes.func.isRequired
};

SavePanel.defaultProps = {
    initialName: '',
    onSaved: () => {}
};

export {OpenPanel, SavePanel};
