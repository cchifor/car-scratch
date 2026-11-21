import {test} from '@playwright/test';
import {openEditor, loadExample, greenFlag, openMonitorTab, waitTel} from '../helpers';

// Runs example 05 "Obstacle avoider" in a simulated world and prints a timeline: commands sent, acks/errs,
// events, and a 4 Hz sample of pose / distance / drive / safety / the "avoiding" variable / the say bubble.
const WORLD = process.env.PROBE_WORLD || 'arena';
const SECONDS = Number(process.env.PROBE_SECONDS || 40);

test(`obstacle avoider in ${WORLD} for ${SECONDS} s`, async ({page}) => {
    const log: string[] = [];
    const t0 = {v: 0};
    const ts = () => ((Date.now() - t0.v) / 1000).toFixed(2).padStart(6);
    page.on('websocket', ws => {
        ws.on('framesent', f => {
            const s = String(f.payload);
            if (t0.v && !s.includes('"t":"hb"')) log.push(`${ts()} >> ${s.slice(0, 120)}`);
        });
        ws.on('framereceived', f => {
            const s = String(f.payload);
            if (t0.v && /"t": ?"(ack|err|done|event)"/.test(s) && !/"t": ?"ack"/.test(s)) log.push(`${ts()} << ${s.slice(0, 140)}`);
        });
    });

    const session = await openEditor(page, {mode: 'sim', world: WORLD});
    await openMonitorTab(page);
    await loadExample(page, process.env.PROBE_EXAMPLE || 'Obstacle avoider', session);
    await page.evaluate(w => (window as any).__picar.link.send('world', {world: w}), WORLD);
    await waitTel(page, `st.world && st.world.id === "${WORLD}"`, 10_000);
    await page.waitForTimeout(500);

    t0.v = Date.now();
    await greenFlag(page);
    const end = Date.now() + SECONDS * 1000;
    while (Date.now() < end) {
        const s = await page.evaluate(() => {
            const w = window as any;
            const tel = w.__picar.link.getTelemetry();
            const rt = w.__picar.runtime;
            const car = rt.targets.find((t: any) => !t.isStage);
            let avoiding: any = '?';
            for (const t of rt.targets) {
                for (const v of Object.values(t.variables) as any[]) if (v.name === 'avoiding') avoiding = v.value;
            }
            const lt = tel.light ? tel.light.bright.join('/') : '';
            const bubble = car && car.getCustomState ? (car.getCustomState('Scratch.looks') || {}).text : '';
            return {
                x: tel.pose.x.toFixed(0), y: tel.pose.y.toFixed(0), dir: tel.pose.dir.toFixed(0),
                dist: tel.dist.cm == null ? '-' : tel.dist.cm.toFixed(0), ds: tel.dist.state,
                drive: tel.drive.dir ? `${tel.drive.dir}@${tel.drive.speed}` : 'stop', steer: tel.steer,
                blocked: tel.safety.blocked, margin: tel.safety.margin_cm, hold: tel.safety.hold,
                bumped: tel.drive.bumped, avoiding, bubble, threads: rt.threads.length, lt
            };
        });
        log.push(`${ts()}    x=${s.x} y=${s.y} dir=${s.dir} dist=${s.dist}(${s.ds}) ${s.drive} steer=${s.steer} ` +
            `blocked=${s.blocked} margin=${s.margin} hold=${s.hold} bumped=${s.bumped} avoiding=${s.avoiding} ` +
            `light=${s.lt} threads=${s.threads} say="${s.bubble || ''}"`);
        await page.waitForTimeout(250);
    }
    await page.evaluate(() => (window as any).__picar.runtime.stopAll());
    console.log(log.join('\n'));
});
