import {expect, test} from '@playwright/test';
import {openEditor, loadExample, greenFlag, openMonitorTab} from '../helpers';

// Visual check: stage mirror on, Obstacle avoider running in the simulator: the Car sprite on the stage moves
// with the simulated car (with a trail), and the Car Monitor plots the obstacle points.
test('stage mirror + obstacle map while the avoider runs', async ({page}, info) => {
    test.setTimeout(120_000);
    const session = await openEditor(page, {mode: 'sim', world: 'arena'});
    await openMonitorTab(page);
    await loadExample(page, 'Obstacle avoider', session);
    await page.evaluate(() => (window as any).__picar.link.send('world', {world: 'arena'}));
    const mirror = page.getByRole('button', {name: /stage mirror/i}).first();
    await mirror.click();
    await expect(mirror).toHaveAttribute('aria-pressed', 'true');
    await page.waitForTimeout(1500);                       // the pen extension loads
    await greenFlag(page);
    const samples: string[] = [];
    for (let i = 0; i < 8; i++) {
        await page.waitForTimeout(1500);
        samples.push(await page.evaluate(() => {
            const w = window as any;
            const tel = w.__picar.link.getTelemetry();
            const car = w.__picar.runtime.targets.find((t: any) => t.isOriginal && t.getName() === 'Car');
            return `pose=(${tel.pose.x.toFixed(0)},${tel.pose.y.toFixed(0)},${tel.pose.dir.toFixed(0)}) ` +
                `sprite=(${car.x.toFixed(0)},${car.y.toFixed(0)},${car.direction.toFixed(0)})`;
        }));
    }
    console.log(samples.join('\n'));
    await page.screenshot({path: info.outputPath('mirror.png')});
    const last = await page.evaluate(() => {
        const w = window as any;
        const tel = w.__picar.link.getTelemetry();
        const car = w.__picar.runtime.targets.find((t: any) => t.isOriginal && t.getName() === 'Car');
        return {dx: Math.abs(tel.pose.x - car.x), dy: Math.abs(tel.pose.y - car.y)};
    });
    expect(last.dx).toBeLessThan(15);
    expect(last.dy).toBeLessThan(15);
    await page.evaluate(() => (window as any).__picar.runtime.stopAll());
});
