import {expect, test, openEditor, waitTel, resume, drive} from '../helpers';

test.describe('visibility', () => {
    test('hidden tab sends pause: the car stops and holds', async ({page}) => {
        await openEditor(page, {mode: 'sim', world: 'arena'});
        await resume(page);
        await drive(page, 'forward', 30);
        await waitTel(page, 'tel.drive.moving', 5000);

        await page.evaluate(() => {
            Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'hidden'});
            Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
            document.dispatchEvent(new Event('visibilitychange'));
        });
        const dt = await waitTel(page, '!tel.drive.moving && tel.safety.hold', 3000, 10);
        expect(dt).toBeLessThan(1500);

        // back to visible: still on hold until Resume
        await page.evaluate(() => {
            Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'visible'});
            Object.defineProperty(document, 'hidden', {configurable: true, get: () => false});
            document.dispatchEvent(new Event('visibilitychange'));
        });
        await page.waitForTimeout(500);
        const tel = await page.evaluate(() => (window as any).__picar.link.getTelemetry());
        expect(tel.safety.hold).toBe(true);
        expect(tel.drive.moving).toBe(false);
    });
});
