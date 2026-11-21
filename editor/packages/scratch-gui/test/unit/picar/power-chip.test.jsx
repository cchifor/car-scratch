import React from 'react';
import {act, fireEvent} from '@testing-library/react';

import {renderWithIntl} from '../../helpers/intl-helpers.jsx';
import CarMonitor from '../../../src/components/car-monitor/car-monitor.jsx';
import {createMockLink} from './mock-link.js';

const POWER_OK = {
    source: 'pi',
    low: false,
    low_since_boot: false,
    low_events: 0,
    throttled: false,
    on_s: 3240,
    motor_s: 180,
    cpu_temp_c: 52.4,
    cpu_load: 0.2
};

describe('Power chip and battery banner', () => {
    beforeEach(() => {
        jest.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    });
    afterEach(() => {
        jest.restoreAllMocks();
    });

    const chip = () => document.querySelector('[data-power]');
    const banner = () => document.querySelector('[data-banner="batteryLow"]');
    const show = power => {
        const link = createMockLink();
        renderWithIntl(<CarMonitor link={link} />);
        act(() => link.emitTelemetry(power ? {power} : {}));
        return link;
    };

    test('low: red "Battery low" chip and the banner', () => {
        show({...POWER_OK, low: true, low_since_boot: true, low_events: 1});
        expect(chip().getAttribute('data-power')).toBe('bad');
        expect(chip().textContent).toBe('Battery low');
        expect(banner()).toBeTruthy();
        expect(banner().textContent).toMatch(/^BATTERY LOW/);
        expect(banner().querySelector('button')).toBeFalsy();
    });

    test('dipped (low_since_boot): amber, no banner', () => {
        show({...POWER_OK, low_since_boot: true});
        expect(chip().getAttribute('data-power')).toBe('warn');
        expect(chip().textContent).toBe('Power dipped');
        expect(banner()).toBeFalsy();
    });

    test('low_events alone also reads as dipped', () => {
        show({...POWER_OK, low_events: 2});
        expect(chip().textContent).toBe('Power dipped');
    });

    test('ok: green "Power OK"', () => {
        show(POWER_OK);
        expect(chip().getAttribute('data-power')).toBe('ok');
        expect(chip().textContent).toBe('Power OK');
        expect(banner()).toBeFalsy();
    });

    test('sim: grey "Simulator" chip', () => {
        show({...POWER_OK, source: 'sim'});
        expect(chip().getAttribute('data-power')).toBe('idle');
        expect(chip().textContent).toBe('Simulator');
    });

    test('source none hides the chip', () => {
        show({...POWER_OK, source: 'none'});
        expect(chip()).toBeNull();
    });

    test('no power field: no chip, no banner', () => {
        show();
        expect(chip()).toBeNull();
        expect(banner()).toBeFalsy();
    });

    test('no telemetry yet: no chip', () => {
        renderWithIntl(<CarMonitor link={createMockLink()} />);
        expect(chip()).toBeNull();
    });

    test('tapping toggles the details panel with usage and help', () => {
        show(POWER_OK);
        expect(document.querySelector('[data-power-details]')).toBeNull();
        expect(chip().getAttribute('aria-expanded')).toBe('false');
        fireEvent.click(chip());
        expect(document.querySelector('[data-power-usage]').textContent)
            .toBe('On for 54 min \u00b7 motors ran 3 min \u00b7 CPU 52 \u00b0C \u00b7 load 20 %');
        expect(document.querySelector('[data-power-details]').textContent)
            .toContain('This car can\'t measure the battery level.');
        expect(chip().getAttribute('aria-expanded')).toBe('true');
        fireEvent.click(chip());
        expect(document.querySelector('[data-power-details]')).toBeNull();
    });

    test('details omit null parts', () => {
        show({...POWER_OK, cpu_temp_c: null, cpu_load: null});
        fireEvent.click(chip());
        expect(document.querySelector('[data-power-usage]').textContent)
            .toBe('On for 54 min \u00b7 motors ran 3 min');
    });

    test('the banner appears only while low and follows telemetry', () => {
        const link = show(POWER_OK);
        expect(banner()).toBeFalsy();
        act(() => link.emitTelemetry({power: {...POWER_OK, low: true}}));
        expect(banner()).toBeTruthy();
        act(() => link.emitTelemetry({power: {...POWER_OK, low: false, low_since_boot: true}}));
        expect(banner()).toBeFalsy();
        expect(chip().textContent).toBe('Power dipped');
    });

    test('the banner sits below E-STOP', () => {
        const link = createMockLink();
        renderWithIntl(<CarMonitor link={link} />);
        act(() => link.emitTelemetry({
            power: {...POWER_OK, low: true},
            safety: {estop: true, hold: true, calibrated: true, obstacle_stop: true}
        }));
        const ids = Array.from(document.querySelectorAll('[data-banner]'))
            .map(el => el.getAttribute('data-banner'));
        expect(ids.indexOf('estop')).toBeGreaterThanOrEqual(0);
        expect(ids.indexOf('estop')).toBeLessThan(ids.indexOf('batteryLow'));
    });
});
