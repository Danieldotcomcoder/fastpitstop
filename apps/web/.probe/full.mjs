import { chromium } from '@playwright/test';
const OUT = process.env.SP;
const browser = await chromium.launch();
const errs = [];
async function visit(url, label, waitMs, viewport = { width: 1500, height: 1200 }) {
  const page = await browser.newPage({ viewport });
  page.on('pageerror', (e) => errs.push(`[${label}][pageerror] ` + String(e).slice(0, 250)));
  page.on('console', (m) => { if (m.type() === 'error') errs.push(`[${label}][console] ` + m.text().slice(0, 250)); });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(waitMs);
  const text = await page.evaluate(() => document.body.innerText.slice(0, 350));
  console.log(`\n===== ${label} =====\n${text}`);
  await page.screenshot({ path: `${OUT}/full-${label}.png`, fullPage: true });
  return page;
}
const p = await visit('http://127.0.0.1:4400/sessions/2024/1?speed=300', 'session', 55000);
console.log('LAP:', await p.getByTestId('session-lap').textContent().catch(() => '?'));
await visit('http://127.0.0.1:4400/live', 'live', 6000);
await visit('http://127.0.0.1:4400/story?key=2024-1-race', 'story', 25000);
await visit('http://127.0.0.1:4400/sessions', 'sessions', 6000);
await visit('http://127.0.0.1:4400/sessions/2024/1?speed=300', 'mobile', 40000, { width: 390, height: 844 });
console.log('\nERRORS:', errs.slice(0, 12).join('\n') || 'none');
await browser.close();
