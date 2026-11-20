import React from 'react';
import {act, fireEvent, screen, waitFor} from '@testing-library/react';

import {renderWithIntl} from '../../helpers/intl-helpers.jsx';
import {OpenPanel, SavePanel} from '../../../src/components/car-projects-modal/car-projects-panels.jsx';
import {isValidProjectName} from '../../../src/lib/picar/projects-api.js';

describe('project names', () => {
    test('accepts the server pattern', () => {
        expect(isValidProjectName('My car_1-b')).toBe(true);
        expect(isValidProjectName('a'.repeat(64))).toBe(true);
    });
    test('rejects everything else', () => {
        expect(isValidProjectName('')).toBe(false);
        expect(isValidProjectName('a'.repeat(65))).toBe(false);
        expect(isValidProjectName('../etc')).toBe(false);
        expect(isValidProjectName('a.sb3')).toBe(false);
        expect(isValidProjectName('café')).toBe(false);
    });
});

describe('SavePanel', () => {
    afterEach(() => {
        delete global.fetch;
    });

    test('invalid name shows a hint and disables Save', () => {
        renderWithIntl(
            <SavePanel
                initialName="bad/name"
                saveSb3={jest.fn()}
                onClose={jest.fn()}
            />
        );
        expect(screen.getByTestId('name-invalid')).toBeTruthy();
        expect(screen.getByRole('button', {name: 'Save'}).disabled).toBe(true);
    });

    test('Save PUTs the sb3 with the sb3 content type', async () => {
        global.fetch = jest.fn(() => Promise.resolve({ok: true, status: 200}));
        const blob = new Blob(['zip']);
        const saveBlob = jest.fn(() => Promise.resolve(blob));
        const onClose = jest.fn();
        const onSaved = jest.fn();
        renderWithIntl(
            <SavePanel
                initialName="Race 1"
                saveSb3={saveBlob}
                onSaved={onSaved}
                onClose={onClose}
            />
        );
        await act(() => {
            fireEvent.click(screen.getByRole('button', {name: 'Save'}));
            return Promise.resolve();
        });
        await waitFor(() => expect(onClose).toHaveBeenCalled());
        const [url, options] = global.fetch.mock.calls[0];
        expect(url).toBe('api/projects/Race%201.sb3');
        expect(options.method).toBe('PUT');
        expect(options.headers['Content-Type']).toBe('application/x.scratch.sb3');
        expect(options.body).toBe(blob);
        expect(onSaved).toHaveBeenCalledWith('Race 1');
    });

    test('a failed save shows the error inline and stays open', async () => {
        global.fetch = jest.fn(() => Promise.resolve({
            ok: false, status: 413, text: () => Promise.resolve('quota')
        }));
        const onClose = jest.fn();
        const saveBig = jest.fn(() => Promise.resolve(new Blob(['x'])));
        renderWithIntl(
            <SavePanel
                initialName="Big"
                saveSb3={saveBig}
                onClose={onClose}
            />
        );
        await act(() => {
            fireEvent.click(screen.getByRole('button', {name: 'Save'}));
            return Promise.resolve();
        });
        const alert = await screen.findByRole('alert');
        expect(alert.textContent).toContain('413');
        expect(onClose).not.toHaveBeenCalled();
    });
});

describe('OpenPanel', () => {
    afterEach(() => {
        delete global.fetch;
    });

    test('lists projects and opens the chosen one', async () => {
        global.fetch = jest.fn(() => Promise.resolve({
            ok: true,
            json: () => Promise.resolve([{name: 'Race 1', size: 10, mtime: 1}, {name: 'Maze', size: 5, mtime: 2}])
        }));
        const onOpenProject = jest.fn(() => Promise.resolve());
        const onClose = jest.fn();
        renderWithIntl(
            <OpenPanel
                onOpenProject={onOpenProject}
                onClose={onClose}
            />
        );
        fireEvent.click(await screen.findByText('Maze'));
        await waitFor(() => expect(onClose).toHaveBeenCalled());
        expect(onOpenProject).toHaveBeenCalledWith('Maze');
    });

    test('shows the error inline when listing fails', async () => {
        global.fetch = jest.fn(() => Promise.resolve({ok: false, status: 500, text: () => Promise.resolve('')}));
        renderWithIntl(
            <OpenPanel
                onOpenProject={jest.fn()}
                onClose={jest.fn()}
            />
        );
        expect((await screen.findByRole('alert')).textContent).toContain('500');
    });
});
