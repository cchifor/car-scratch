import {expect, test as base, type Page, type APIRequestContext, type TestInfo} from '@playwright/test';

/* eslint-disable @typescript-eslint/no-explicit-any */

export interface Example {id: string; title: string; description?: string; world?: string}

// Console errors that are known to be benign. Anything else of type 'error' fails the smoke test.
export const BENIGN_CONSOLE_ERRORS: RegExp[] = [
    // A favicon / manifest / icon request the minimal server may not serve (Chromium and WebKit wording).
    /Failed to load resource: the server responded with a status of 404/,
    /Failed to load resource: .*404/,
    // Headless chromium without a GPU: Scratch's renderer falls back to software GL and warns.
    /GPU stall due to ReadPixels/,
    // Autoplay policy: Scratch's audio engine resumes its AudioContext on the first gesture.
    /The AudioContext was not allowed to start/
];

export interface Session {
    errors: string[];
    dialogs: string[];
}

/** Records console errors, page errors and dialogs for a page. */
export function watch(page: Page): Session {
    const s: Session = {errors: [], dialogs: []};
    page.on('console', msg => {
        if (msg.type() === 'error') s.errors.push(msg.text());
    });
    page.on('pageerror', err => s.errors.push(`pageerror: ${err.message}`));
    page.on('dialog', async dialog => {
        // Scratch's normal "Replace contents of the current project?" confirm is accepted; any other
        // dialog (e.g. the example load-failure alert) is recorded as an error and dismissed.
        if (dialog.type() === 'confirm' && /Replace contents/i.test(dialog.message())) {
            await dialog.accept().catch(() => {});
            return;
        }
        s.dialogs.push(`${dialog.type()}: ${dialog.message()}`);
        await dialog.dismiss().catch(() => {});
    });
    return s;
}

export function unexpectedErrors(s: Session): string[] {
    return s.errors.filter(e => !BENIGN_CONSOLE_ERRORS.some(re => re.test(e)));
}

export interface OpenOptions {
    mode?: 'car' | 'sim';
    world?: string;
    /** Extra query string, e.g. "x=1". */
    query?: string;
}

/**
 * Opens the editor with the debug hooks, in the requested mode, and waits for the link to be open
 * (and, for the sim, for the world). A fresh context has fresh localStorage, so the mode is forced
 * through an init script.
 */
export async function openEditor(page: Page, opts: OpenOptions = {}): Promise<Session> {
    const mode = opts.mode ?? 'sim';
    const session = watch(page);
    await page.addInitScript((m: string) => {
        try {
            if (!window.localStorage.getItem('picar.mode')) window.localStorage.setItem('picar.mode', m);
        } catch { /* storage may be unavailable */ }
    }, mode);
    await page.goto(`/?picar_debug=1${opts.query ? `&${opts.query}` : ''}`);
    await page.waitForFunction(() => Boolean((window as any).__picar && (window as any).__picar.link), null, {timeout: 60_000});
    await waitLinkOpen(page, mode);
    if (mode === 'sim' && opts.world) await setWorld(page, opts.world);
    return session;
}

export async function waitLinkOpen(page: Page, mode?: 'car' | 'sim', timeout = 30_000) {
    await page.waitForFunction((m: string | null) => {
        const link = (window as any).__picar.link;
        const st = link.getState();
        return st.connection === 'open' && (!m || st.mode === m) && link.getTelemetry() !== null;
    }, mode ?? null, {timeout});
}

export async function setWorld(page: Page, world: string) {
    await page.evaluate((w: string) => (window as any).__picar.link.send('world', {world: w}), world);
    await page.waitForFunction((w: string) => {
        const st = (window as any).__picar.link.getState();
        return st.world && st.world.id === w;
    }, world, {timeout: 15_000});
    await page.waitForTimeout(300);
}

/** The link state snapshot. */
export const getState = (page: Page) => page.evaluate(() => (window as any).__picar.link.getState());
/** The last telemetry frame (or null). */
export const getTel = (page: Page) => page.evaluate(() => (window as any).__picar.link.getTelemetry());

/**
 * Polls inside the page until the JS expression `body` (which can use `tel` and `st`) is truthy.
 * Returns the elapsed milliseconds.
 */
export async function waitTel(page: Page, body: string, timeout = 10_000, polling = 25): Promise<number> {
    const t0 = Date.now();
    await page.waitForFunction(
        `(() => { const link = window.__picar.link; const tel = link.getTelemetry(); const st = link.getState(); return !!tel && (${body}); })()`,
        null, {timeout, polling});
    return Date.now() - t0;
}

/** Records every link 'event' into window.__events (call before the action under test). */
export async function recordEvents(page: Page) {
    await page.evaluate(() => {
        const w = window as any;
        w.__events = w.__events || [];
        if (!w.__eventsHooked) {
            w.__eventsHooked = true;
            w.__picar.link.on('event', (e: any) => w.__events.push(e));
        }
    });
}
export const events = (page: Page): Promise<any[]> => page.evaluate(() => (window as any).__events || []);

/** Hold is cleared by the green flag; tests that drive through the link do it explicitly. */
export async function resume(page: Page) {
    await page.evaluate(() => (window as any).__picar.link.send('resume', {}));
    await waitTel(page, '!tel.safety.hold', 5000);
}

export async function drive(page: Page, dir: 'forward' | 'backward', speed: number) {
    await page.evaluate(([d, s]) => (window as any).__picar.link.send('drive', {dir: d, speed: s}), [dir, speed] as const);
}

/**
 * In car mode make sure this page is the controller. The shared car may keep the previous test's
 * 30 s lease, so wait briefly and then take control (recorded as an annotation so it is visible).
 */
export async function ensureController(page: Page, info: TestInfo) {
    try {
        await page.waitForFunction(() => (window as any).__picar.link.getState().role === 'controller', null, {timeout: 4000});
    } catch {
        info.annotations.push({type: 'lease', description: 'car mode: new session was a viewer, took control with force'});
        await page.evaluate(() => (window as any).__picar.link.control('take', true));
        await page.waitForFunction(() => (window as any).__picar.link.getState().role === 'controller', null, {timeout: 5000});
    }
    // E-stop on ANY session latches the shared car and survives hand-over (by design): earlier tests'
    // sim e-stops leave it latched, so clear it before driving.
    await page.evaluate(() => (window as any).__picar.link.send('estop_reset', {}));
    await page.waitForFunction(() => {
        const t = (window as any).__picar.link.getTelemetry();
        return t && !t.safety.estop;
    }, null, {timeout: 5000});
}

/** Best-effort cleanup of the shared car: stop and clear the e-stop latch. */
export async function cleanCar(page: Page) {
    try {
        await page.evaluate(() => {
            const l = (window as any).__picar.link;
            l.send('stop', {});
            l.send('estop_reset', {});
        });
    } catch { /* page may be gone */ }
}

export async function fetchExamples(request: APIRequestContext): Promise<Example[]> {
    const r = await request.get('/examples/index.json');
    expect(r.ok(), 'examples/index.json is served').toBeTruthy();
    const data = await r.json();
    return Array.isArray(data) ? data : data.examples;
}

const targetIds = (page: Page): Promise<string[]> => page.evaluate(() => (window as any).__picar.runtime.targets.map((t: any) => t.id));

/** Examples menu, then the example by title. Resolves once the project was replaced in the VM. */
export async function loadExample(page: Page, title: string, session?: Session) {
    const before = await targetIds(page);
    const menu = page.getByRole('button', {name: 'Examples menu'});
    if ((await menu.getAttribute('aria-expanded')) !== 'true') await menu.click();
    await page.getByText(title, {exact: true}).first().click();
    await page.waitForFunction((old: string[]) => {
        const t = (window as any).__picar.runtime.targets;
        return t.length > 0 && t.every((x: any) => !old.includes(x.id));
    }, before, {timeout: 30_000});
    await page.waitForTimeout(500);
    // The menu stays open after a pick (product quirk, reported): close it so the next action is not blocked.
    if ((await menu.getAttribute('aria-expanded')) === 'true') await menu.click();
    if (session) expect(session.dialogs, `no dialog while loading "${title}"`).toEqual([]);
}

export async function greenFlag(page: Page) {
    await page.evaluate(() => (window as any).__picar.runtime.greenFlag());
}

export async function openMonitorTab(page: Page) {
    await page.getByRole('tab', {name: 'Pi Car'}).click();
    await expect(page.getByRole('region', {name: 'Car monitor'})).toBeVisible();
}

/** Freeze the heartbeat: the link keeps running but never sends `hb` (the page's JS "hangs"). */
export async function freezeHeartbeat(page: Page) {
    await page.evaluate(() => {
        const link = (window as any).__picar.link;
        if (link.__origRawSend) return;
        link.__origRawSend = link._rawSend;
        link._rawSend = function (obj: any) {
            if (obj && obj.t === 'hb') return true;
            return link.__origRawSend.call(link, obj);
        };
    });
}

export async function thawHeartbeat(page: Page) {
    await page.evaluate(() => {
        const link = (window as any).__picar.link;
        if (link.__origRawSend) {
            link._rawSend = link.__origRawSend;
            delete link.__origRawSend;
        }
    });
}

export const test = base;
export {expect};
