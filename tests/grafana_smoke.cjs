// Active Grafana browser check; run with Playwright available.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const baseURL = process.env.GRAFANA_URL || 'http://grafana:3000';
  const run = process.env.RULESHIFT_SMOKE_RUN;
  assert(run, 'Set RULESHIFT_SMOKE_RUN to an existing simulated shortcut run');
  assert.match(run, /^[a-f0-9]{32}$/, 'Expected a generated run ID');
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const context = await browser.newContext({ baseURL, viewport: { width: 1600, height: 1100 } });
    const login = await context.request.post('/login', {
      data: { user: 'admin', password: process.env.GRAFANA_PASSWORD || 'ruleshift-local-dev' },
    });
    assert.equal(login.status(), 200, 'Grafana login');
    const dashboard = JSON.parse(fs.readFileSync(path.join(__dirname, '../monitoring/grafana/dashboards/ruleshift.json')));
    const queries = dashboard.panels.flatMap(panel => (panel.targets || []).map(target => ({
      refId: String(panel.id), datasource: target.datasource, format: 'table', maxDataPoints: 500,
      rawSql: target.rawSql.replaceAll('${run:sqlstring}', `'${run}'`),
    })));
    const queryResponse = await context.request.post('/api/ds/query', {
      data: { queries, from: 'now-7d', to: 'now' },
    });
    assert.equal(queryResponse.status(), 200, 'Every panel SQL request succeeds');
    const allResults = (await queryResponse.json()).results;
    assert.equal(Object.keys(allResults).length, queries.length);
    for (const [id, result] of Object.entries(allResults)) {
      assert(!result.error, `Panel ${id}: ${result.error}`);
    }
    const page = await context.newPage();
    const errors = [];
    page.on('response', async response => {
      if (!response.url().includes('/api/ds/query')) return;
      if (!response.ok()) errors.push(`Datasource HTTP ${response.status()}`);
      try {
        const data = await response.json();
        for (const result of Object.values(data.results || {})) {
          if (result.error) errors.push(result.error);
        }
      } catch {}
    });
    await page.goto(`/d/ruleshift/ruleshift?var-mode=simulated&var-run=${encodeURIComponent(run)}`, {
      waitUntil: 'domcontentloaded',
    });
    await page.getByText('Correct / all attempted', { exact: true }).waitFor({ timeout: 30000 });
    await page.waitForFunction(() => document.body.innerText.includes('76.1%'), { timeout: 30000 });
    await page.screenshot({ path: '/artifacts/docs/dashboard-overview.png' });
    await page.getByText('Failure microscope / inspect a cell to read the full value', { exact: true }).scrollIntoViewIfNeeded();
    await page.getByText('Expected', { exact: true }).first().waitFor({ timeout: 15000 });
    await page.screenshot({ path: '/artifacts/docs/dashboard-failures.png' });
    assert.deepEqual(errors, [], 'No Grafana datasource errors');
    console.log(`PASS: all ${queries.length} panel SQL queries, login, selected run, rendered accuracy, failure table, no datasource errors; two screenshots saved.`);
    await context.close();
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
