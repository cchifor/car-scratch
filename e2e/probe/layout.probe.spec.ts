import {expect, test} from '@playwright/test';
import {openEditor, loadExample, fetchExamples} from '../helpers';

// Every example, every sprite: the top-level scripts in the code area must not overlap. Screenshots of each
// example's Car sprite code go to the test output folder.
test('example scripts never overlap in the code area', async ({page, request}, info) => {
    test.setTimeout(600_000);
    const examples = await fetchExamples(request);
    const session = await openEditor(page, {mode: 'sim', world: 'arena'});
    const problems: string[] = [];
    for (const ex of examples) {
        await loadExample(page, ex.title, session);
        const targets: {id: string, name: string}[] = await page.evaluate(() =>
            (window as any).__picar.runtime.targets.filter((t: any) => t.isOriginal && !t.isStage)
                .map((t: any) => ({id: t.id, name: t.getName()})));
        for (const t of targets) {
            await page.locator('[class*="sprite-selector-item_sprite-name"]', {hasText: new RegExp(`^${t.name}$`)})
                .first().click();
            await page.waitForTimeout(700);
            const boxes = await page.evaluate(() => {
                const canvas = document.querySelector('.blocklyWorkspace .blocklyBlockCanvas');
                if (!canvas) return [];
                return Array.from(canvas.children)
                    .filter(g => g.tagName === 'g' && (g as Element).getAttribute('data-id'))
                    .map(g => {
                        const r = (g as Element).getBoundingClientRect();
                        return {x: r.x, y: r.y, w: r.width, h: r.height};
                    })
                    .filter(r => r.w > 0 && r.h > 0);
            });
            for (let i = 0; i < boxes.length; i++) {
                for (let j = i + 1; j < boxes.length; j++) {
                    const a = boxes[i];
                    const b = boxes[j];
                    const overlap = a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
                    if (overlap) problems.push(`${ex.id} / ${t.name}: scripts ${i} and ${j} overlap`);
                }
            }
            console.log(`${ex.id} / ${t.name}: ${boxes.length} scripts checked`);
            if (t.name === 'Car') await page.screenshot({path: info.outputPath(`${ex.id}.png`)});
        }
    }
    console.log(problems.length ? problems.join('\n') : 'no overlaps');
    expect(problems).toEqual([]);
});
