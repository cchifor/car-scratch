import {test} from '@playwright/test';
import {openEditor, loadExample, greenFlag, openMonitorTab, waitTel, fetchExamples} from '../helpers';

// Runs every example in its own simulator world for PROBE_SECONDS after the green flag and prints a summary:
// path length, net displacement, heading change, steering used, directions used, bumps, errors by code,
// events, the sequence of distinct say-bubble texts and final variable values.
const SECONDS = Number(process.env.PROBE_SECONDS || 20);

test('all examples in the simulator', async ({page, request}) => {
    test.setTimeout(900_000);
    const examples = await fetchExamples(request);
    const session = await openEditor(page, {mode: 'sim', world: 'arena'});
    await openMonitorTab(page);
    const errs: Record<string, number> = {};
    const evs: Record<string, number> = {};
    page.on('websocket', ws => ws.on('framereceived', f => {
        const s = String(f.payload);
        const e = /"t": ?"err", ?"code": ?"([a-z_]+)"/.exec(s);
        if (e) errs[e[1]] = (errs[e[1]] || 0) + 1;
        const v = /"t": ?"event", ?"kind": ?"([a-z_]+)"/.exec(s);
        if (v) evs[v[1]] = (evs[v[1]] || 0) + 1;
    }));
    for (const ex of examples) {
        await loadExample(page, ex.title, session);
        const world = ex.world || 'arena';
        await page.evaluate(w => (window as any).__picar.link.send('world', {world: w}), world);
        await waitTel(page, `st.world && st.world.id === "${world}"`, 10_000);
        await page.waitForTimeout(400);
        for (const k of Object.keys(errs)) delete errs[k];
        for (const k of Object.keys(evs)) delete evs[k];
        await greenFlag(page);
        const says: string[] = [];
        const dirs = new Set<string>();
        const steers = new Set<number>();
        let path = 0;
        let bumps = 0;
        let prev: any = null;
        let first: any = null;
        let headingTurn = 0;
        const end = Date.now() + SECONDS * 1000;
        while (Date.now() < end) {
            const s = await page.evaluate(() => {
                const w = window as any;
                const tel = w.__picar.link.getTelemetry();
                const rt = w.__picar.runtime;
                const car = rt.targets.find((t: any) => !t.isStage && t.isOriginal);
                const bubble = car && car.getCustomState ? (car.getCustomState('Scratch.looks') || {}).text : '';
                return {x: tel.pose.x, y: tel.pose.y, dir: tel.pose.dir, drive: tel.drive.dir, steer: tel.steer,
                    bumped: tel.drive.bumped, bubble};
            });
            if (!first) first = s;
            if (prev) {
                path += Math.hypot(s.x - prev.x, s.y - prev.y);
                let d = s.dir - prev.dir;
                while (d > 180) d -= 360;
                while (d < -180) d += 360;
                headingTurn += Math.abs(d);
            }
            prev = s;
            if (s.drive) dirs.add(s.drive);
            steers.add(Math.round(s.steer));
            if (s.bumped) bumps++;
            if (s.bubble && says[says.length - 1] !== s.bubble) says.push(String(s.bubble).slice(0, 40));
            await page.waitForTimeout(200);
        }
        const vars = await page.evaluate(() => {
            const r: Record<string, any> = {};
            for (const t of (window as any).__picar.runtime.targets) {
                for (const v of Object.values(t.variables) as any[]) if (v.type === '') r[v.name] = v.value;
            }
            return r;
        });
        await page.evaluate(() => (window as any).__picar.runtime.stopAll());
        await page.waitForTimeout(300);
        console.log(`== ${ex.id} (${world}): path=${path.toFixed(0)}cm net=${Math.hypot(prev.x - first.x, prev.y - first.y).toFixed(0)}cm ` +
            `turned=${headingTurn.toFixed(0)}deg dirs=[${[...dirs]}] steer=[${[...steers].sort((a, b) => a - b)}] ` +
            `bumpedSamples=${bumps}\n   errs=${JSON.stringify(errs)} events=${JSON.stringify(evs)}\n   says=${JSON.stringify(says.slice(0, 8))}` +
            `\n   vars=${JSON.stringify(vars)}`);
    }
});
