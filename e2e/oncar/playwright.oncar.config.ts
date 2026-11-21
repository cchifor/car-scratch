import {defineConfig, devices} from '@playwright/test';

// On-car checks against the REAL car (BASE_URL=http://<car>:8080). Never part of the default suite.
// These specs must never send a motion command (drive / move / steer / resume): they run with the
// car on the floor or the bench, without anyone watching it.
export default defineConfig({
    testDir: '.',
    testMatch: /\.oncar\.spec\.ts/,
    fullyParallel: false,
    workers: 1,
    retries: 0,
    timeout: 120_000,
    reporter: [['list']],
    use: {
        baseURL: process.env.BASE_URL,
        screenshot: 'on',
        trace: 'retain-on-failure',
        navigationTimeout: 60_000
    },
    projects: [
        {name: 'chromium', use: {...devices['Desktop Chrome'], viewport: {width: 1280, height: 800}}},
        {name: 'ipad-pro-11', use: {...devices['iPad Pro 11 landscape']}}
    ]
});
