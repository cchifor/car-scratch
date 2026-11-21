import {defineConfig, devices} from '@playwright/test';

// Diagnostic probes against the simulator (BASE_URL, default the compose sim-server): they record what an
// example does over time and print a timeline. Not part of the default suite.
export default defineConfig({
    testDir: '.',
    testMatch: /\.probe\.spec\.ts/,
    workers: 1,
    retries: 0,
    timeout: 180_000,
    reporter: [['list']],
    use: {
        baseURL: process.env.BASE_URL || 'http://sim-server:8080',
        ...devices['Desktop Chrome'],
        viewport: {width: 1280, height: 800},
        screenshot: 'off'
    }
});
