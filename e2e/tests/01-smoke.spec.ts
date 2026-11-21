import {expect, test, openEditor, unexpectedErrors} from '../helpers';

test.describe('smoke', () => {
    test('editor loads: title, Pi Car category, Car Monitor tab, E-STOP, no console errors', async ({page}) => {
        const session = await openEditor(page, {mode: 'sim'});

        await expect(page).toHaveTitle(/Car Scratch/);

        // Pi Car category in the block palette (scratch-blocks category menu).
        await expect(page.locator('xpath=//div[normalize-space(text())="Pi Car"]')).toBeVisible({timeout: 30_000});

        // Car Monitor tab next to Sprites.
        await expect(page.getByRole('tab', {name: 'Sprites'})).toBeVisible();
        await expect(page.getByRole('tab', {name: 'Pi Car'})).toBeVisible();

        // E-STOP in the menu bar.
        await expect(page.getByRole('button', {name: 'Emergency stop the car', exact: true}).first()).toBeVisible();

        // Link is up and talking.
        const st = await page.evaluate(() => (window as any).__picar.link.getState());
        expect(st.connection).toBe('open');

        await page.waitForTimeout(1500); // let late errors surface
        expect(unexpectedErrors(session), 'unexpected console errors').toEqual([]);
    });

    test('server status endpoint and examples index', async ({request}) => {
        const r = await request.get('/api/status');
        expect(r.ok()).toBeTruthy();
        const status = await r.json();
        expect(status.car).toBeTruthy();
        const ex = await request.get('/examples/index.json');
        expect(ex.ok()).toBeTruthy();
    });
});
