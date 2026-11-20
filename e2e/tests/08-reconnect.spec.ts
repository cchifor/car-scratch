import {expect, test, openEditor, waitTel, resume, drive, ensureController, cleanCar} from '../helpers';

test.describe('reconnect', () => {
    test('socket closed from the page: closed -> open within 5 s, car still on hold', async ({page}, testInfo) => {
        await openEditor(page, {mode: 'car'});
        await ensureController(page, testInfo);
        await resume(page);
        await drive(page, 'forward', 30);
        await waitTel(page, 'tel.drive.moving', 5000);
        await page.getByRole('tab', {name: 'Pi Car'}).click();

        await page.evaluate(() => {
            const w = window as any;
            w.__conn = [];
            w.__picar.link.subscribe((s: any) => w.__conn.push(s.connection));
            w.__t0 = performance.now();
            w.__picar.link._ws.close();
        });

        await page.waitForFunction(() => {
            const c = (window as any).__conn;
            return c.includes('closed') || c.includes('connecting');
        }, null, {timeout: 3000});
        await page.waitForFunction(() => {
            const link = (window as any).__picar.link;
            return link.getState().connection === 'open' && link.getTelemetry() !== null;
        }, null, {timeout: 5000});
        const took = await page.evaluate(() => performance.now() - (window as any).__t0);
        expect(took, `reconnect took ${Math.round(took)} ms`).toBeLessThan(5000);

        // The car stopped on disconnect and must not resume by itself.
        const tel = await page.evaluate(() => (window as any).__picar.link.getTelemetry());
        expect(tel.drive.moving).toBe(false);
        expect(tel.safety.hold, 'hold latched after the disconnect').toBe(true);
        await expect(page.locator('[data-banner="paused"]').first()).toBeVisible();
        await cleanCar(page);
    });
});
