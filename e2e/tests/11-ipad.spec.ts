import {expect, test, openEditor, loadExample, greenFlag, waitTel, resume} from '../helpers';

// Runs only in the WebKit iPad projects (see playwright.config.ts). Playwright WebKit on Linux is not
// iPad Safari: real-device Stage 4 of docs/on-car-checklist.md remains the gate.
test.describe('iPad layout and touch', () => {
    test('no horizontal overflow', async ({page}) => {
        await openEditor(page, {mode: 'sim', world: 'arena'});
        const m = await page.evaluate(() => ({sw: document.documentElement.scrollWidth, iw: window.innerWidth}));
        expect(m.sw, `scrollWidth ${m.sw} vs innerWidth ${m.iw}`).toBeLessThanOrEqual(m.iw + 1);
        await page.getByRole('tab', {name: 'Pi Car'}).tap();
        const m2 = await page.evaluate(() => ({sw: document.documentElement.scrollWidth, iw: window.innerWidth}));
        expect(m2.sw, `monitor tab: scrollWidth ${m2.sw} vs innerWidth ${m2.iw}`).toBeLessThanOrEqual(m2.iw + 1);
    });

    test('touch targets: E-STOP and the Pi Car tab are at least 44x44', async ({page}) => {
        await openEditor(page, {mode: 'sim', world: 'arena'});
        const targets = [
            ['E-STOP', page.getByRole('button', {name: /Emergency stop/i}).first()],
            ['Pi Car tab', page.getByRole('tab', {name: 'Pi Car'})]
        ] as const;
        for (const [label, loc] of targets) {
            await expect(loc, label).toBeVisible();
            const box = await loc.boundingBox();
            expect(box, label).toBeTruthy();
            expect(box!.width, `${label} width ${box!.width}`).toBeGreaterThanOrEqual(44);
            expect(box!.height, `${label} height ${box!.height}`).toBeGreaterThanOrEqual(44);
        }
    });

    test('tapping the Pi Car tab shows the monitor', async ({page}) => {
        await openEditor(page, {mode: 'sim', world: 'arena'});
        await page.getByRole('tab', {name: 'Pi Car'}).tap();
        await expect(page.getByRole('region', {name: 'Car monitor'})).toBeVisible();
        await expect(page.getByRole('tab', {name: 'Pi Car'})).toHaveAttribute('aria-selected', 'true');
    });

    test('example 02 Touch remote: tapping the arrow sprite moves the sim car', async ({page}) => {
        const session = await openEditor(page, {mode: 'sim', world: 'arena'});
        await loadExample(page, 'Touch remote', session);
        await greenFlag(page);
        await resume(page);

        const before = (await page.evaluate(() => (window as any).__picar.link.getTelemetry())).pose;

        // stage coordinates -> page coordinates of the stage canvas
        const pos = await page.evaluate(() => {
            const t = (window as any).__picar.runtime.targets.find((x: any) => x.sprite && x.sprite.name === 'Forward');
            return {x: t.x, y: t.y};
        });
        const canvas = page.locator('[class*="stage_stage"] canvas').first();
        await expect(canvas).toBeVisible();
        const box = (await canvas.boundingBox())!;
        const px = box.x + ((pos.x + 240) / 480) * box.width;
        const py = box.y + ((180 - pos.y) / 360) * box.height;
        await page.touchscreen.tap(px, py);

        await waitTel(page, `Math.hypot(tel.pose.x - ${before.x}, tel.pose.y - ${before.y}) > 1`, 10_000);
        const after = await page.evaluate(() => (window as any).__picar.link.getTelemetry());
        expect(Math.hypot(after.pose.x - before.x, after.pose.y - before.y)).toBeGreaterThan(1);
    });
});
