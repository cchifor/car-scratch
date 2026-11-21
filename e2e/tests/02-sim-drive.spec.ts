import {expect, test, openEditor, loadExample, greenFlag, waitTel, recordEvents, events, openMonitorTab} from '../helpers';

test.describe('simulator drive', () => {
    test('example 04 Obstacle stop: the sim car moves and stops before the wall', async ({page}) => {
        const session = await openEditor(page, {mode: 'sim', world: 'corridor'});
        await openMonitorTab(page);
        await recordEvents(page);
        await loadExample(page, 'Obstacle stop', session);
        // the example selects its world when in sim mode
        await waitTel(page, 'st.world && st.world.id === "corridor"', 10_000);

        const x0 = (await page.evaluate(() => (window as any).__picar.link.getTelemetry())).pose.x;
        await greenFlag(page);

        // moves: pose x increases
        await waitTel(page, `tel.drive.moving && tel.pose.x > ${x0 + 5}`, 15_000);

        // stops: obstacle_stop event or not moving with dist ok
        await page.waitForFunction(() => {
            const w = window as any;
            return (w.__events || []).some((e: any) => e.kind === 'obstacle_stop') ||
                !w.__picar.link.getTelemetry().drive.moving;
        }, null, {timeout: 30_000});
        await waitTel(page, '!tel.drive.moving', 10_000);

        const tel = await page.evaluate(() => (window as any).__picar.link.getTelemetry());
        const stopCm = tel.safety.stop_cm;
        expect(tel.drive.moving).toBe(false);
        expect(tel.dist.cm, 'stopped before the wall, not into it').toBeGreaterThanOrEqual(stopCm - 5);
        expect(tel.pose.x).toBeGreaterThan(x0 + 5);
        // The program stops the car itself (it reacts at 45 cm, outside the safety margin) and says where the
        // wall is. 2026-11-21: it used to wait for 30 cm, the safety stop got there first and it never spoke.
        await page.waitForFunction(() => {
            const rt = (window as any).__picar.runtime;
            const car = rt.targets.find((t: any) => !t.isStage && t.isOriginal);
            const text = car && (car.getCustomState('Scratch.looks') || {}).text;
            return typeof text === 'string' && text.startsWith('I stopped! The wall is');
        }, null, {timeout: 5000});
        expect((await events(page)).some((e: any) => e.kind === 'obstacle_stop'),
            'the program stopped first, not the safety stop').toBe(false);

        // never bumped during the whole run
        await page.waitForTimeout(500);
        const after = await page.evaluate(() => (window as any).__picar.link.getTelemetry());
        expect(after.drive.bumped ?? false).toBe(false);
    });
});
