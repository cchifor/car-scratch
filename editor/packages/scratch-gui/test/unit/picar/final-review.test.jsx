import React from 'react';
import {act, fireEvent, screen} from '@testing-library/react';

import {renderWithIntl} from '../../helpers/intl-helpers.jsx';
import CarMonitor from '../../../src/components/car-monitor/car-monitor.jsx';
import RotateHint from '../../../src/components/car-monitor/rotate-hint.jsx';
import {withoutScratchCharacters} from '../../../src/lib/picar/scratch-characters.js';
import spriteLibrary from '../../../src/lib/libraries/sprites.json';
import costumeLibrary from '../../../src/lib/libraries/costumes.json';
import {createMockLink} from './mock-link.js';

const banner = id => document.querySelector(`[data-banner="${id}"]`);

describe('hardware fault banner', () => {
    beforeEach(() => {
        jest.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    });
    afterEach(() => {
        jest.restoreAllMocks();
    });

    test('shows from tel.safety.hw_fault with a Reset that sends estop_reset', () => {
        const link = createMockLink();
        renderWithIntl(<CarMonitor link={link} />);
        act(() => link.emitTelemetry({
            safety: {estop: false, hold: false, calibrated: true, obstacle_stop: true, hw_fault: 'motor'}
        }));
        expect(banner('hwfault')).toBeTruthy();
        expect(banner('hwfault').textContent)
            .toContain('Hardware fault — lift the car or switch it off. Reset when safe.');
        fireEvent.click(banner('hwfault').querySelector('button'));
        expect(link.send).toHaveBeenCalledWith('estop_reset', {});
    });

    test('shows from state.safety.hw_fault before any telemetry; a viewer gets no Reset', () => {
        const link = createMockLink({
            role: 'viewer',
            safety: {estop: false, hold: false, calibrated: true, blocked: false, hw_fault: true}
        });
        renderWithIntl(<CarMonitor link={link} />);
        expect(banner('hwfault')).toBeTruthy();
        expect(banner('hwfault').querySelector('button')).toBeFalsy();
    });

    test('is absent without a fault', () => {
        const link = createMockLink();
        renderWithIntl(<CarMonitor link={link} />);
        act(() => link.emitTelemetry());
        expect(banner('hwfault')).toBeFalsy();
    });
});

describe('RotateHint', () => {
    test('carries a working E-STOP and stays dismissible', () => {
        const link = createMockLink();
        renderWithIntl(<RotateHint
            forceVisible
            link={link}
        />);
        fireEvent.pointerDown(screen.getByRole('button', {name: 'Emergency stop the car'}));
        expect(link.estop).toHaveBeenCalledTimes(1);
        fireEvent.click(screen.getByRole('button', {name: 'Dismiss'}));
        expect(screen.queryByRole('button', {name: 'Emergency stop the car'})).toBeNull();
    });
});

describe('Scratch characters', () => {
    test('filters sprites and costumes by name prefix', () => {
        const items = [{name: 'Cat'}, {name: 'Cat 2'}, {name: 'Gobo'}, {name: 'Pico walking'}, {name: 'Nano'},
            {name: 'Tera'}, {name: 'Giga'}, {name: 'Car'}, {name: 'Robot'}];
        expect(withoutScratchCharacters(items).map(i => i.name)).toEqual(['Car', 'Robot']);
    });

    test('the bundled libraries lose their characters', () => {
        const pattern = /^(cat|gobo|pico|nano|tera|giga)/i;
        expect(spriteLibrary.some(i => pattern.test(i.name))).toBe(true);
        expect(withoutScratchCharacters(spriteLibrary).some(i => pattern.test(i.name))).toBe(false);
        expect(withoutScratchCharacters(costumeLibrary).some(i => pattern.test(i.name))).toBe(false);
    });
});
