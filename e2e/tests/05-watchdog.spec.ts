import {expect, test, openEditor, waitTel, resume, drive, freezeHeartbeat, thawHeartbeat, getState} from '../helpers';

test.describe('watchdog / hold', () => {
    test('frozen heartbeat: motion stops within ~1 s, hold latched, PAUSED banner, Resume works', async ({page}) => {
        await openEditor(page, {mode: 'sim', world: 'arena'});
        await page.getByRole('tab', {name: 'Pi Car'}).click();
        await resume(page);
        await drive(page, 'forward', 30);
        await waitTel(page, 'tel.drive.moving', 5000);

        await freezeHeartbeat(page);
        const t0 = Date.now();
        await waitTel(page, '!tel.drive.moving && tel.safety.hold', 3000, 10);
        const dt = Date.now() - t0;
        expect(dt, `watchdog stop took ${dt} ms`).toBeLessThan(1500);

        const banner = page.locator('[data-banner="paused"]');
        await expect(banner).toBeVisible();
        await expect(banner.getByRole('button', {name: 'Resume'})).toBeVisible();

        // motion refused while on hold
        await drive(page, 'forward', 30);
        await page.waitForFunction(() => {
            const e = (window as any).__picar.link.getState().lastError;
            return e && e.code === 'hold';
        }, null, {timeout: 5000});

        // The server closes sockets silent for 10 s; resume the heartbeat before pressing Resume.
        await thawHeartbeat(page);
        await banner.getByRole('button', {name: 'Resume'}).click();
        await waitTel(page, '!tel.safety.hold', 5000);
        await expect(banner).toHaveCount(0);
        await drive(page, 'forward', 30);
        await waitTel(page, 'tel.drive.moving', 5000);
        expect((await getState(page)).safety.hold).toBe(false);
    });
});
