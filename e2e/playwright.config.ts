import {defineConfig, devices} from '@playwright/test';

// Car Scratch e2e. Runs only in Docker (docker compose run --rm e2e) against the `sim-server`
// service, which serves the editor and a simulated shared car (--hardware sim).
//
// One worker, no file parallelism: the "car" mode tests share ONE simulated car, and the iPad
// projects must not race the desktop one for it.
//
// WebKit headless on Linux has no WebGL in some builds; if the iPad projects fail at the stage
// canvas, run them headed under xvfb:
//   xvfb-run -a npx playwright test --project="ipad-pro-11" --headed

const baseURL = process.env.BASE_URL || 'http://localhost:8080';
const ipad = (name: string, device: string) => ({
    name,
    testMatch: /(smoke|ipad)\.spec\.ts/,
    use: {...devices[device]}
});

export default defineConfig({
    testDir: './tests',
    // Quick smoke first, then the rest alphabetically (files are numbered).
    fullyParallel: false,
    workers: 1,
    retries: 1,
    timeout: 90_000,
    expect: {timeout: 15_000},
    reporter: [
        ['list'],
        ['html', {outputFolder: process.env.PW_REPORT_DIR || '/out/report', open: 'never'}],
        ['json', {outputFile: process.env.PW_JSON_FILE || '/out/results.json'}]
    ],
    use: {
        baseURL,
        trace: 'retain-on-failure',
        screenshot: 'only-on-failure',
        actionTimeout: 20_000,
        navigationTimeout: 60_000,
        // Shared storage-less context: every test gets a fresh context (no cookies / localStorage).
        storageState: undefined
    },
    projects: [
        {
            name: 'chromium',
            testIgnore: /ipad\.spec\.ts/,
            use: {...devices['Desktop Chrome'], viewport: {width: 1280, height: 800}}
        },
        ipad('ipad-pro-11', 'iPad Pro 11 landscape'),
        ipad('ipad-gen7', 'iPad (gen 7) landscape'),
        ipad('ipad-mini', 'iPad Mini landscape')
    ]
});
