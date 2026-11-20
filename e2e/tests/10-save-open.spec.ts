import {expect, test, openEditor, loadExample} from '../helpers';

const NAME = 'e2e-test';

test.describe('save / open on the car', () => {
    test('Save to car as "e2e-test", then Open from car lists and loads it', async ({page, request}) => {
        const session = await openEditor(page, {mode: 'sim', world: 'arena'});

        // File menu
        await page.getByText('File', {exact: true}).first().click();
        const saveItem = page.getByText('Save to car…');
        test.skip(await saveItem.count() === 0, 'feature not present in this build: "Save to car…" is not in the File menu');

        // save a recognisable project (Touch remote has a "Forward" sprite)
        await page.keyboard.press('Escape');
        await loadExample(page, 'Touch remote', session);

        await page.getByText('File', {exact: true}).first().click();
        await page.getByText('Save to car…').click();
        await page.getByLabel('Project name').fill(NAME);
        await page.getByRole('button', {name: 'Save', exact: true}).click();

        await expect.poll(async () => {
            const r = await request.get('/api/projects');
            const list = await r.json() as Array<{name: string}>;
            return list.some(p => p.name === NAME);
        }, {timeout: 20_000, message: 'project stored on the car'}).toBe(true);
        // dialog closes
        await expect(page.getByLabel('Project name')).toHaveCount(0, {timeout: 10_000});

        // load something else, then open from the car
        await loadExample(page, 'Hello car', session);
        expect(await page.evaluate(() => (window as any).__picar.runtime.targets.some((t: any) => t.sprite && t.sprite.name === 'Forward'))).toBe(false);

        await page.getByText('File', {exact: true}).first().click();
        await page.getByText('Open from car…').click();
        await page.getByRole('button', {name: NAME}).click();
        await page.waitForFunction(() =>
            (window as any).__picar.runtime.targets.some((t: any) => t.sprite && t.sprite.name === 'Forward'),
        null, {timeout: 30_000});
        expect(session.dialogs).toEqual([]);

        // cleanup
        const del = await request.delete(`/api/projects/${NAME}.sb3`);
        expect([200, 204]).toContain(del.status());
    });
});
