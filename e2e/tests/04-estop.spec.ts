import {expect, test, openEditor, waitTel, resume, drive, getState} from '../helpers';

test.describe('e-stop', () => {
    test('E-STOP button latches, stops motion, refuses motion until Reset in the banner', async ({page}) => {
        await openEditor(page, {mode: 'sim', world: 'arena'});
        // the banners live in the monitor
        await page.getByRole('tab', {name: 'Pi Car'}).click();
        await resume(page);
        await drive(page, 'forward', 40);
        await waitTel(page, 'tel.drive.moving', 5000);

        const estop = page.getByRole('button', {name: /Emergency stop/i}).first();
        const box = await estop.boundingBox();
        expect(box).toBeTruthy();
        // pointerdown is what fires it: press without releasing
        await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
        await page.mouse.down();
        await waitTel(page, 'tel.safety.estop && !tel.drive.moving', 2000, 10);
        await page.mouse.up();

        const banner = page.locator('[data-banner="estop"]');
        await expect(banner).toBeVisible();

        // motion refused with code estop
        await drive(page, 'forward', 40);
        await page.waitForFunction(() => {
            const e = (window as any).__picar.link.getState().lastError;
            return e && e.code === 'estop';
        }, null, {timeout: 5000});
        expect((await page.evaluate(() => (window as any).__picar.link.getTelemetry())).drive.moving).toBe(false);

        // Reset in the banner
        await banner.getByRole('button', {name: 'Reset'}).click();
        await waitTel(page, '!tel.safety.estop', 5000);
        await expect(banner).toHaveCount(0);
        await drive(page, 'forward', 30);
        await waitTel(page, 'tel.drive.moving', 5000);
        expect((await getState(page)).safety.estop).toBe(false);
    });
});
