import React from 'react';
import {act, fireEvent, screen} from '@testing-library/react';

import {renderWithIntl} from '../../helpers/intl-helpers.jsx';
import CarMonitor from '../../../src/components/car-monitor/car-monitor.jsx';
import {createMockLink} from './mock-link.js';

const makeVm = (loaded = false) => ({
    extensionManager: {
        isExtensionLoaded: jest.fn(() => loaded),
        loadExtensionURL: jest.fn()
    }
});

describe('CarMonitor toolbar', () => {
    beforeEach(() => {
        jest.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
        window.localStorage.clear();
    });
    afterEach(() => {
        jest.restoreAllMocks();
    });

    test('Reset position sends reset_pose for a controller', () => {
        const link = createMockLink();
        renderWithIntl(<CarMonitor link={link} />);
        fireEvent.click(screen.getByText('Reset position'));
        expect(link.send).toHaveBeenCalledWith('reset_pose', {});
    });

    test('Reset position is hidden for a viewer', () => {
        const link = createMockLink({role: 'viewer'});
        renderWithIntl(<CarMonitor link={link} />);
        expect(screen.queryByText('Reset position')).toBeNull();
        expect(screen.getByText('Clear map')).toBeTruthy();
    });

    test('Clear map sends nothing to the car', () => {
        const link = createMockLink();
        renderWithIntl(<CarMonitor link={link} />);
        act(() => link.emitTelemetry());
        fireEvent.click(screen.getByText('Clear map'));
        expect(link.send).not.toHaveBeenCalled();
    });

    test('Stage mirror is hidden when the link has no setStageMirror', () => {
        const link = createMockLink();
        renderWithIntl(<CarMonitor link={link} />);
        expect(screen.queryByText(/Stage mirror/)).toBeNull();
    });

    test('Stage mirror turns on, loading Pen when it is missing', () => {
        const link = createMockLink({stageMirror: false}, {stageMirror: true});
        const vm = makeVm(false);
        renderWithIntl(<CarMonitor
            link={link}
            vm={vm}
        />);
        const button = screen.getByText(/Stage mirror/).closest('button');
        expect(button.getAttribute('aria-pressed')).toBe('false');
        fireEvent.click(button);
        expect(vm.extensionManager.loadExtensionURL).toHaveBeenCalledWith('pen');
        expect(link.setStageMirror).toHaveBeenCalledWith(true);
    });

    test('Stage mirror does not reload Pen, and turns off without touching it', () => {
        const link = createMockLink({stageMirror: false}, {stageMirror: true});
        const vm = makeVm(true);
        renderWithIntl(<CarMonitor
            link={link}
            vm={vm}
        />);
        fireEvent.click(screen.getByText(/Stage mirror/).closest('button'));
        expect(vm.extensionManager.loadExtensionURL).not.toHaveBeenCalled();
        act(() => link.setState({stageMirror: true}));
        const button = screen.getByText(/Stage mirror/).closest('button');
        expect(button.getAttribute('aria-pressed')).toBe('true');
        fireEvent.click(button);
        expect(link.setStageMirror).toHaveBeenLastCalledWith(false);
    });

    test('Room is the default view and Follow persists', () => {
        const link = createMockLink();
        renderWithIntl(<CarMonitor link={link} />);
        const room = screen.getByText('Room').closest('button');
        expect(room.getAttribute('aria-pressed')).toBe('true');
        fireEvent.click(screen.getByText('Follow'));
        const follow = screen.getByText('Follow').closest('button');
        expect(follow.getAttribute('aria-pressed')).toBe('true');
        expect(window.localStorage.getItem('picar.monitorView')).toBe('follow');
    });
});
