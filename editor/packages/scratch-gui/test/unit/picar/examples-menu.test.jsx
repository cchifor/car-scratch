import React from 'react';
import {fireEvent, screen, waitFor} from '@testing-library/react';
import configureStore from 'redux-mock-store';
import {Provider} from 'react-redux';

import {renderWithIntl} from '../../helpers/intl-helpers.jsx';
import {MenuRefProvider} from '../../../src/contexts/menu-ref-context.jsx';
import ExamplesMenu from '../../../src/components/menu-bar/examples-menu.jsx';
import {menuInitialState} from '../../../src/reducers/menus';
import {LoadingState} from '../../../src/reducers/project-state';
import {createMockLink} from './mock-link.js';

describe('ExamplesMenu', () => {
    let store;
    let vm;
    let link;

    beforeEach(() => {
        link = createMockLink({mode: 'sim'});
        vm = {
            loadProject: jest.fn(() => Promise.resolve()),
            runtime: {peripheralExtensions: {picar: {link}}},
            on: jest.fn(),
            removeListener: jest.fn()
        };
        store = configureStore()({
            locales: {isRtl: false, locale: 'en'},
            scratchGui: {
                menus: menuInitialState,
                projectChanged: false,
                projectState: {loadingState: LoadingState.SHOWING_WITHOUT_ID},
                modals: {},
                vm
            }
        });
    });
    afterEach(() => {
        delete global.fetch;
        jest.restoreAllMocks();
    });

    const renderMenu = () => renderWithIntl(
        <Provider store={store}>
            <MenuRefProvider>
                <ExamplesMenu depth={1} />
            </MenuRefProvider>
        </Provider>
    );
    const open = () => fireEvent.click(screen.getByRole('button', {name: 'Examples menu'}));

    test('lists the examples from examples/index.json (relative URL)', async () => {
        global.fetch = jest.fn(() => Promise.resolve({
            ok: true,
            json: () => Promise.resolve([
                {id: '01-hello', title: 'Hello car', description: 'd', level: 1, needs: [], world: null},
                {id: '04-obstacle', title: 'Obstacle stop', description: 'd', level: 2, needs: [], world: 'corridor'}
            ])
        }));
        renderMenu();
        open();
        expect(await screen.findByText('Hello car')).toBeTruthy();
        expect(screen.getByText('Obstacle stop')).toBeTruthy();
        expect(global.fetch.mock.calls[0][0]).toBe('examples/index.json');
    });

    test('shows "No examples" when the index is missing', async () => {
        global.fetch = jest.fn(() => Promise.resolve({ok: false, status: 404}));
        renderMenu();
        open();
        await waitFor(() => expect(global.fetch).toHaveBeenCalled());
        expect(await screen.findByText('No examples')).toBeTruthy();
    });

    test('shows "No examples" when the fetch fails', async () => {
        global.fetch = jest.fn(() => Promise.reject(new Error('offline')));
        renderMenu();
        open();
        expect(await screen.findByText('No examples')).toBeTruthy();
    });

    test('choosing an example loads it and selects its world in sim mode', async () => {
        const buffer = new ArrayBuffer(4);
        global.fetch = jest.fn(url => Promise.resolve(
            url === 'examples/index.json' ?
                {ok: true, json: () => Promise.resolve([{id: '04-obstacle', title: 'Obstacle stop', world: 'corridor'}])} :
                {ok: true, arrayBuffer: () => Promise.resolve(buffer)}
        ));
        renderMenu();
        open();
        fireEvent.click(await screen.findByText('Obstacle stop'));
        await waitFor(() => expect(vm.loadProject).toHaveBeenCalledWith(buffer));
        await waitFor(() => expect(link.send).toHaveBeenCalledWith('world', {world: 'corridor'}));
        expect(global.fetch).toHaveBeenCalledWith('examples/04-obstacle.sb3');
        const types = store.getActions().map(a => a.type);
        expect(types.length).toBeGreaterThan(2);
    });

    test('does not select a world when the link is in car mode', async () => {
        link.setState({mode: 'car'});
        global.fetch = jest.fn(url => Promise.resolve(
            url === 'examples/index.json' ?
                {ok: true, json: () => Promise.resolve([{id: 'x', title: 'X', world: 'arena'}])} :
                {ok: true, arrayBuffer: () => Promise.resolve(new ArrayBuffer(1))}
        ));
        renderMenu();
        open();
        fireEvent.click(await screen.findByText('X'));
        await waitFor(() => expect(vm.loadProject).toHaveBeenCalled());
        expect(link.send).not.toHaveBeenCalled();
    });

    describe('closing', () => {
        const indexOnly = () => {
            global.fetch = jest.fn(url => Promise.resolve(
                url === 'examples/index.json' ?
                    {ok: true, json: () => Promise.resolve([{id: 'x', title: 'X'}])} :
                    {ok: true, arrayBuffer: () => Promise.resolve(new ArrayBuffer(1))}
            ));
        };
        const button = () => screen.getByRole('button', {name: 'Examples menu'});

        test('choosing an item closes the menu', async () => {
            indexOnly();
            renderMenu();
            open();
            expect(button().getAttribute('aria-expanded')).toBe('true');
            const item = await screen.findByText('X');
            fireEvent.pointerDown(item);
            fireEvent.pointerUp(item);
            fireEvent.click(item);
            await waitFor(() => expect(button().getAttribute('aria-expanded')).toBe('false'));
            await waitFor(() => expect(vm.loadProject).toHaveBeenCalled());
        });

        test('keyboard activation of an item (click without pointerdown) closes the menu', async () => {
            indexOnly();
            renderMenu();
            open();
            fireEvent.click(await screen.findByText('X'));
            await waitFor(() => expect(button().getAttribute('aria-expanded')).toBe('false'));
        });

        test('clicking the menu button again closes it', async () => {
            indexOnly();
            renderMenu();
            fireEvent.pointerDown(button());
            fireEvent.pointerUp(button());
            fireEvent.click(button());
            expect(button().getAttribute('aria-expanded')).toBe('true');
            fireEvent.pointerDown(button());
            fireEvent.pointerUp(button());
            fireEvent.click(button());
            await waitFor(() => expect(button().getAttribute('aria-expanded')).toBe('false'));
        });

        test('clicking outside closes it', async () => {
            indexOnly();
            renderMenu();
            open();
            expect(button().getAttribute('aria-expanded')).toBe('true');
            fireEvent.pointerUp(document.body);
            await waitFor(() => expect(button().getAttribute('aria-expanded')).toBe('false'));
        });
    });
});
