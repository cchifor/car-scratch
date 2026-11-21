import React from 'react';
import {act, fireEvent, screen} from '@testing-library/react';

import {renderWithIntl} from '../../helpers/intl-helpers.jsx';
import CarEstop from '../../../src/containers/car-estop.jsx';
import CarMonitor from '../../../src/components/car-monitor/car-monitor.jsx';
import {createMockLink} from './mock-link.js';

const latch = link => act(() => link.emitTelemetry({
    safety: {estop: true, hold: false, calibrated: true, obstacle_stop: true}
}));
const vmFor = link => ({
    runtime: {peripheralExtensions: {picar: {link}}},
    on: jest.fn(),
    removeListener: jest.fn()
});
const resetButton = () => screen.queryByRole('button', {name: 'Reset emergency stop'});
const estopButton = () => screen.getByRole('button', {name: 'Emergency stop the car'});

describe('CarEstop reset', () => {
    test('controller + latched: Reset sends exactly one estop_reset and no estop', () => {
        const link = createMockLink();
        renderWithIntl(<CarEstop vm={vmFor(link)} />);
        latch(link);
        expect(resetButton()).toBeTruthy();
        expect(resetButton().textContent).toBe('Reset');
        fireEvent.click(resetButton());
        expect(link.send).toHaveBeenCalledTimes(1);
        expect(link.send).toHaveBeenCalledWith('estop_reset', {});
        expect(link.estop).not.toHaveBeenCalled();
    });

    test('E-STOP while latched still sends estop, never estop_reset, and shows the latched state', () => {
        const link = createMockLink();
        renderWithIntl(<CarEstop vm={vmFor(link)} />);
        latch(link);
        expect(estopButton().getAttribute('aria-pressed')).toBe('true');
        expect(estopButton().getAttribute('title')).toBe('E-STOP is on');
        fireEvent.pointerDown(estopButton());
        expect(link.estop).toHaveBeenCalledTimes(1);
        expect(link.send).not.toHaveBeenCalled();
    });

    test('not latched: no Reset', () => {
        const link = createMockLink();
        renderWithIntl(<CarEstop vm={vmFor(link)} />);
        act(() => link.emitTelemetry());
        expect(resetButton()).toBeNull();
        expect(estopButton().getAttribute('aria-pressed')).toBeNull();
    });

    test('viewer + latched: no Reset, and the banner explains why', () => {
        jest.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
        const link = createMockLink({role: 'viewer'});
        renderWithIntl(
            <div>
                <CarEstop vm={vmFor(link)} />
                <CarMonitor link={link} />
            </div>
        );
        latch(link);
        expect(resetButton()).toBeNull();
        const banner = document.querySelector('[data-banner="estop"]');
        expect(banner.textContent).toContain('Only the driver can reset it');
        jest.restoreAllMocks();
    });

    test('floating variant renders Reset when latched for the controller', () => {
        const link = createMockLink();
        renderWithIntl(
            <CarEstop
                floating
                vm={vmFor(link)}
            />
        );
        latch(link);
        fireEvent.click(resetButton());
        expect(link.send).toHaveBeenCalledWith('estop_reset', {});
    });
});
