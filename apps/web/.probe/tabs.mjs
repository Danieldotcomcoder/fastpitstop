import { chromium } from '@playwright/test';
const OUT = process.env.SP;
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1500, height: 1100 } });
const errs = [];
page.on('pageerror', (e) => errs.push('[pageerror] ' + String(e).slice(0, 200)));
page.on('console', (m) => { if (m.type() === 'error') errs.push('[console] ' + m.text().slice(0, 200)); });
await page.goto('http://127.0.0.1:4400/sessions/2024/1?speed=200', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(60000);
const lap = await page.getByTestId('session-lap').textContent().catch(() => '?');
console.log('LAP after 60s:', lap);
for (const label of ['Race positions', 'Tyre performance', 'Pit strategy', 'Braking']) {
  await page.getByRole('button', { name: new RegExp(label) }).click();
  await page.waitForTimeout(6000);
  const stage = await page.locator('.stage-content').innerText();
  console.log(`\n===== ${label} =====`);
  console.log(stage.slice(0, 700));
  await page.screenshot({ path: `${OUT}/tab-${label.split(' ')[0]}.png`, fullPage: true });
}
console.log('\nERRORS:', errs.slice(0, 10).join('\n') || 'none');
await browser.close();
