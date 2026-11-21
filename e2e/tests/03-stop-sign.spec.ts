import {expect, test, openEditor, loadExample, greenFlag, waitTel, resume, drive} from '../helpers';

test.describe('stop sign', () => {
    test('the GUI stop button stops the car in < 300 ms', async ({page}) => {
        const session = await openEditor(page, {mode: 'sim', world: 'arena'});
        await loadExample(page, 'Hello car', session);
        await greenFlag(page);
        await waitTel(page, 'tel.drive.moving', 15_000);

        const stopSign = page.locator('[class*="stop-all_stop-all"]').first();
        await expect(stopSign).toBeVisible();
        const t0 = Date.now();
        await stopSign.click();
        await waitTel(page, '!tel.drive.moving', 3000, 10);
        const dt = Date.now() - t0;
        expect(dt, `stop took ${dt} ms`).toBeLessThan(300);
    });

    test('stop sign also stops a motion started by a drive command', async ({page}) => {
        await openEditor(page, {mode: 'sim', world: 'arena'});
        await resume(page);
        await drive(page, 'forward', 40);
        await waitTel(page, 'tel.drive.moving', 5000);
        const t0 = Date.now();
        await page.locator('[class*="stop-all_stop-all"]').first().click();
        await waitTel(page, '!tel.drive.moving', 3000, 10);
        expect(Date.now() - t0).toBeLessThan(300);
    });
});
