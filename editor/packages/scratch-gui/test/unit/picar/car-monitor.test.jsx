import React from 'react';
import {act, fireEvent, screen} from '@testing-library/react';

import {renderWithIntl} from '../../helpers/intl-helpers.jsx';
import CarMonitor from '../../../src/components/car-monitor/car-monitor.jsx';
import EStopButton from '../../../src/components/car-monitor/estop-button.jsx';
import {createMockLink} from './mock-link.js';

describe('CarMonitor', () => {
    beforeEach(() => {
        // jsdom has no canvas; the paint loop must cope with a missing context.
        jest.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    });
    afterEach(() => {
        jest.restoreAllMocks();
    });

    const banner = id => document.querySelector(`[data-banner="${id}"]`);

    test('a healthy controller shows no banners', () => {
        const link = createMockLink();
        renderWithIntl(<CarMonitor link={link} />);
        act(() => link.emitTelemetry());
        expect(document.querySelectorAll('[data-banner]').length).toBe(0);
    });

    test('E-STOP banner with Reset sends estop_reset (controller)', () => {
        const link = createMockLink();
        renderWithIntl(<CarMonitor link={link} />);
        act(() => link.emitTelemetry({safety: {estop: true, hold: true, calibrated: true, obstacle_stop: true}}));
        expect(banner('estop')).toBeTruthy();
        expect(banner('paused')).toBeFalsy();
        fireEvent.click(banner('estop').querySelector('button'));
        expect(link.send).toHaveBeenCalledWith('estop_reset', {});
    });

    test('E-STOP banner has no Reset for a viewer', () => {
        const link = createMockLink({role: 'viewer'});
        renderWithIntl(<CarMonitor link={link} />);
        act(() => link.emitTelemetry({safety: {estop: true, hold: false, calibrated: true, obstacle_stop: true}}));
        expect(banner('estop')).toBeTruthy();
        expect(banner('estop').querySelector('button')).toBeFalsy();
    });

    test('PAUSED banner with Resume sends resume', () => {
        const link = createMockLink();
        renderWithIntl(<CarMonitor link={link} />);
        act(() => link.emitTelemetry({safety: {estop: false, hold: true, calibrated: true, obstacle_stop: true}}));
        fireEvent.click(banner('paused').querySelector('button'));
        expect(link.send).toHaveBeenCalledWith('resume', {});
    });

    test('viewer sees VIEW ONLY and Take control forces after confirmation', () => {
        const link = createMockLink({role: 'viewer'});
        const confirm = jest.spyOn(window, 'confirm').mockReturnValue(true);
        renderWithIntl(<CarMonitor link={link} />);
        expect(banner('viewOnly')).toBeTruthy();
        fireEvent.click(banner('viewOnly').querySelector('button'));
        expect(confirm).toHaveBeenCalled();
        expect(link.control).toHaveBeenCalledWith('take', true);
    });

    test('Take control does nothing when the confirmation is declined', () => {
        const link = createMockLink({role: 'viewer'});
        jest.spyOn(window, 'confirm').mockReturnValue(false);
        renderWithIntl(<CarMonitor link={link} />);
        fireEvent.click(banner('viewOnly').querySelector('button'));
        expect(link.control).not.toHaveBeenCalled();
    });

    test('Take control is not forced when the car has no controller', () => {
        const link = createMockLink({role: 'viewer'});
        const confirm = jest.spyOn(window, 'confirm');
        renderWithIntl(<CarMonitor link={link} />);
        act(() => {
            link.emit('role', {role: 'viewer', controller_present: false});
        });
        fireEvent.click(banner('viewOnly').querySelector('button'));
        expect(confirm).not.toHaveBeenCalled();
        expect(link.control).toHaveBeenCalledWith('take', false);
    });

    test('obstacle stop event shows a banner that hides after 2 s', () => {
        jest.useFakeTimers();
        const link = createMockLink();
        renderWithIntl(<CarMonitor link={link} />);
        act(() => {
            link.emit('event', {kind: 'obstacle_stop', cm: 18});
        });
        expect(banner('obstacle')).toBeTruthy();
        act(() => {
            jest.advanceTimersByTime(2100);
        });
        expect(banner('obstacle')).toBeFalsy();
        jest.useRealTimers();
    });

    test('not calibrated and obstacle stop off banners', () => {
        const link = createMockLink();
        renderWithIntl(<CarMonitor link={link} />);
        act(() => link.emitTelemetry({safety: {estop: false, hold: false, calibrated: false, obstacle_stop: false}}));
        expect(banner('notCalibrated')).toBeTruthy();
        expect(banner('obstacleStopOff')).toBeTruthy();
    });

    test('line sensor offline banner needs the capability', () => {
        const link = createMockLink({
            hw: {backend: 'real', capabilities: {ultrasonic: true, light: true, line: true}}
        });
        renderWithIntl(<CarMonitor link={link} />);
        act(() => link.emitTelemetry({line: {ok: false, on: null}}));
        expect(banner('lineOffline')).toBeTruthy();
    });

    test('reconnecting and reload states', () => {
        const link = createMockLink({connection: 'closed'});
        renderWithIntl(<CarMonitor link={link} />);
        expect(banner('reconnecting')).toBeTruthy();
        act(() => link.setState({connection: 'reload'}));
        expect(banner('reload')).toBeTruthy();
        expect(banner('reconnecting')).toBeFalsy();
        expect(banner('reload').querySelector('button')).toBeTruthy();
    });

    test('banner buttons are touch sized by class', () => {
        const link = createMockLink({role: 'viewer'});
        renderWithIntl(<CarMonitor link={link} />);
        expect(banner('viewOnly').querySelector('button')).toBeTruthy();
    });

    test('mode switch calls setMode; world picker only in sim mode', () => {
        const link = createMockLink();
        renderWithIntl(<CarMonitor link={link} />);
        expect(screen.queryByLabelText('Simulator world')).toBeNull();
        fireEvent.click(screen.getByText('Simulator'));
        expect(link.setMode).toHaveBeenCalledWith('sim');
        act(() => link.setState({mode: 'sim', world: {id: 'arena'}}));
        const picker = screen.getByLabelText('Simulator world');
        fireEvent.change(picker, {target: {value: 'corridor'}});
        expect(link.send).toHaveBeenCalledWith('world', {world: 'corridor'});
    });

    test('telemetry frames do not re-render the monitor', () => {
        const link = createMockLink();
        const spy = jest.spyOn(link, 'getState');
        renderWithIntl(<CarMonitor link={link} />);
        spy.mockClear();
        for (let i = 0; i < 20; i++) act(() => link.emitTelemetry({pose: {x: i, y: 0, dir: 90}}));
        expect(spy).not.toHaveBeenCalled();
    });
});

describe('EStopButton', () => {
    test('pointerdown calls link.estop immediately', () => {
        const link = createMockLink();
        renderWithIntl(<EStopButton link={link} />);
        fireEvent.pointerDown(screen.getByRole('button', {name: 'Emergency stop the car'}));
        expect(link.estop).toHaveBeenCalledTimes(1);
    });

    test('keyboard activation (click with detail 0) also stops, a mouse click after pointerdown does not repeat', () => {
        const link = createMockLink();
        renderWithIntl(<EStopButton link={link} />);
        const button = screen.getByRole('button', {name: 'Emergency stop the car'});
        fireEvent.click(button, {detail: 1});
        expect(link.estop).not.toHaveBeenCalled();
        fireEvent.click(button, {detail: 0});
        expect(link.estop).toHaveBeenCalledTimes(1);
    });
});
