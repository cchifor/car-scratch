import {expect, test, openEditor, waitTel, resume, drive, ensureController, cleanCar} from '../helpers';

// Both contexts attach to the ONE shared car (the server runs --hardware sim).
test.describe('viewer', () => {
    test('second tab is VIEW ONLY; viewer E-STOP stops the car', async ({browser}, testInfo) => {
        const ctxA = await browser.newContext();
        const ctxB = await browser.newContext();
        const a = await ctxA.newPage();
        const b = await ctxB.newPage();
        try {
            await openEditor(a, {mode: 'car'});
            await ensureController(a, testInfo);
            await openEditor(b, {mode: 'car'});

            expect(await b.evaluate(() => (window as any).__picar.link.getState().role)).toBe('viewer');
            expect(await a.evaluate(() => (window as any).__picar.link.getState().role)).toBe('controller');

            await b.getByRole('tab', {name: 'Pi Car'}).click();
            await expect(b.locator('[data-banner="viewOnly"]')).toBeVisible();
            await expect(b.locator('[data-banner="viewOnly"]')).toContainText('VIEW ONLY');

            // viewer cannot drive
            await b.evaluate(() => (window as any).__picar.link.send('drive', {dir: 'forward', speed: 30}));
            await b.waitForFunction(() => {
                const e = (window as any).__picar.link.getState().lastError;
                return e && e.code === 'not_controller';
            }, null, {timeout: 5000});

            // controller drives
            await resume(a);
            await drive(a, 'forward', 30);
            await waitTel(a, 'tel.drive.moving', 5000);

            // viewer E-STOP stops the shared car
            await b.getByRole('button', {name: /Emergency stop/i}).first().click();
            await waitTel(a, 'tel.safety.estop && !tel.drive.moving', 3000, 10);
            await waitTel(b, 'tel.safety.estop', 3000, 10);
        } finally {
            await cleanCar(a);
            await ctxA.close();
            await ctxB.close();
        }
    });
});
