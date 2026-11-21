import React from 'react';
import {act, fireEvent, screen} from '@testing-library/react';

import {renderWithIntl} from '../../helpers/intl-helpers.jsx';
import CarMonitor from '../../../src/components/car-monitor/car-monitor.jsx';
import {createMockLink} from './mock-link.js';

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
        fireEvent.click(screen.getByLabelText('Reset position'));
        expect(link.send).toHaveBeenCalledWith('reset_pose', {});
    });

    test('Reset position is hidden for a viewer', () => {
        const link = createMockLink({role: 'viewer'});
        renderWithIntl(<CarMonitor link={link} />);
        expect(screen.queryByLabelText('Reset position')).toBeNull();
        expect(screen.getByLabelText('Clear map')).toBeTruthy();
    });

    test('Clear map sends nothing to the car and calls clearTrail when present', () => {
        const link = createMockLink({}, {clearTrail: true});
        renderWithIntl(<CarMonitor link={link} />);
        act(() => link.emitTelemetry());
        fireEvent.click(screen.getByLabelText('Clear map'));
        expect(link.send).not.toHaveBeenCalled();
        expect(link.clearTrail).toHaveBeenCalledTimes(1);
    });

    test('Clear map works when the link has no clearTrail', () => {
        const link = createMockLink();
        renderWithIntl(<CarMonitor link={link} />);
        expect(() => fireEvent.click(screen.getByLabelText('Clear map'))).not.toThrow();
    });

    test('Stage mirror is hidden when the link has no setStageMirror', () => {
        const link = createMockLink();
        renderWithIntl(<CarMonitor link={link} />);
        expect(screen.queryByLabelText('Stage mirror')).toBeNull();
    });

    test('Stage mirror toggles on and off', () => {
        const link = createMockLink({stageMirror: false}, {stageMirror: true});
        renderWithIntl(<CarMonitor link={link} />);
        const button = screen.getByLabelText('Stage mirror');
        expect(button.getAttribute('aria-pressed')).toBe('false');
        fireEvent.click(button);
        expect(link.setStageMirror).toHaveBeenCalledWith(true);
        act(() => link.setState({stageMirror: true}));
        expect(screen.getByLabelText('Stage mirror').getAttribute('aria-pressed')).toBe('true');
        fireEvent.click(screen.getByLabelText('Stage mirror'));
        expect(link.setStageMirror).toHaveBeenLastCalledWith(false);
    });

    test('Room | Follow is shown only in Car mode', () => {
        const link = createMockLink({mode: 'sim'});
        renderWithIntl(<CarMonitor link={link} />);
        expect(screen.queryByText('Room')).toBeNull();
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
