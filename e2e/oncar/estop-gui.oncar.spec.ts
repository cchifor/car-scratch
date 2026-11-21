import {expect, test, type Page} from '@playwright/test';

// The owner's flow on the real car, with no motion command: open the editor as a first-time user
// (default mode), get control (by promotion if a previous controller's lease is still held), press
// E-STOP in the menu bar, then Reset next to it. Logs the link state, the visible banners and every
// non-heartbeat WebSocket frame the page sends.

const state = (page: Page) => page.evaluate(() => {
    const l = (window as any).__picar.link;
    const st = l.getState();
    const tel = l.getTelemetry();
    return {
        connection: st.connection, mode: st.mode, role: st.role, lastError: st.lastError,
        estop: tel && tel.safety.estop, hold: tel && tel.safety.hold, hw_fault: tel && tel.safety.hw_fault
    };
});
const banners = (page: Page) => page.locator('[data-banner]').evaluateAll(
    els => els.filter(e => (e as HTMLElement).offsetParent !== null)
        .map(e => `${e.getAttribute('data-banner')}: ${(e.textContent || '').trim().slice(0, 80)}`));

test('E-STOP then Reset from the menu bar on the real car (no motion)', async ({page}, info) => {
    const sent: string[] = [];
    page.on('websocket', ws => {
        ws.on('framesent', f => {
            const s = String(f.payload);
            if (!s.includes('"t":"hb"')) sent.push(s.slice(0, 160));
        });
    });
    page.on('console', m => { if (m.type() === 'error') console.log(`console.error: ${m.text().slice(0, 200)}`); });

    await page.goto('/?picar_debug=1');
    await page.waitForFunction(() => Boolean((window as any).__picar && (window as any).__picar.link), null, {timeout: 60_000});
    await page.waitForFunction(() => {
        const l = (window as any).__picar.link;
        return l.getState().connection === 'open' && l.getTelemetry() !== null;
    }, null, {timeout: 30_000});
    console.log(`[${info.project.name}] after load:`, JSON.stringify(await state(page)));

    // A previous controller's 30 s lease may still be held: the server promotes this tab when it expires.
    const t0 = Date.now();
    await page.waitForFunction(() => (window as any).__picar.link.getState().role === 'controller', null, {timeout: 45_000});
    console.log(`controller after ${Math.round((Date.now() - t0) / 1000)} s`);

    await page.getByRole('tab', {name: 'Sprites'}).click();      // the owner's case: monitor banner not visible
    // exact name: "Reset emergency stop" (next to it while latched) must not match
    const estop = page.getByRole('button', {name: 'Emergency stop the car', exact: true}).filter({visible: true}).first();
    await estop.click();
    await page.waitForFunction(() => (window as any).__picar.link.getTelemetry().safety.estop === true, null, {timeout: 5000});
    console.log('after E-STOP:', JSON.stringify(await state(page)));
    await expect(estop).toHaveAttribute('aria-pressed', 'true');
    const reset = page.getByRole('button', {name: 'Reset emergency stop'}).first();
    await expect(reset).toBeVisible();
    const box = await reset.boundingBox();
    expect(box && box.width >= 44 && box.height >= 44, `reset target ${JSON.stringify(box)}`).toBeTruthy();
    await page.screenshot({path: info.outputPath('1-estop-latched.png')});

    await reset.click();
    await page.waitForFunction(() => (window as any).__picar.link.getTelemetry().safety.estop === false, null, {timeout: 5000});
    await expect(reset).toHaveCount(0);
    const after = await state(page);
    console.log('after Reset:', JSON.stringify(after));
    await page.getByRole('tab', {name: 'Pi Car'}).click();
    await page.waitForTimeout(500);
    console.log('banners:', JSON.stringify(await banners(page)));
    await page.screenshot({path: info.outputPath('2-reset.png')});
    console.log('frames sent (no hb):\n  ' + sent.join('\n  '));

    expect(after.estop).toBe(false);
    expect(after.hold, 'still paused: nothing moves without Resume').toBe(true);
    expect(sent.some(s => /"op":"(drive|move|steer|resume)"/.test(s)), 'no motion command was sent').toBe(false);
});
