import {expect, test, openEditor, loadExample, fetchExamples} from '../helpers';

test.describe('examples', () => {
    test('every example in examples/index.json loads from the Examples menu without an error dialog', async ({page, request}) => {
        const examples = await fetchExamples(request);
        expect(examples.length).toBeGreaterThanOrEqual(6);
        test.setTimeout(60_000 + examples.length * 30_000);

        const session = await openEditor(page, {mode: 'sim', world: 'arena'});
        for (const ex of examples) {
            await test.step(`load ${ex.id} (${ex.title})`, async () => {
                await loadExample(page, ex.title, session);
                // the loaded project uses the picar extension and has at least one script
                const picarBlocks = await page.evaluate(() => {
                    const t = (window as any).__picar.runtime.targets;
                    let n = 0;
                    for (const target of t) {
                        for (const b of Object.values(target.blocks._blocks) as any[]) {
                            if (/^picar_/.test(b.opcode)) n++;
                        }
                    }
                    return n;
                });
                expect(picarBlocks, `${ex.id} has Pi Car blocks`).toBeGreaterThan(0);
                // no modal error (e.g. the project loader's failure dialog)
                await expect(page.locator('[class*="modal_modal-content"] [role="alert"]')).toHaveCount(0);
            });
        }
        expect(session.dialogs).toEqual([]);
    });
});
