import { chromium } from '@playwright/test';
const OUT = process.env.SP;
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
await page.goto('http://127.0.0.1:4400/sessions/2024/1?speed=300', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(40000);
await page.screenshot({ path: `${OUT}/m-top.png` });          // viewport only
await page.evaluate(() => window.scrollBy(0, 700));
await page.waitForTimeout(400);
await page.screenshot({ path: `${OUT}/m-mid.png` });
// check for real overlap of the header chrome over the tower
const overlap = await page.evaluate(() => {
  const pill = document.querySelector('.delay-sync-trigger');
  const tower = document.querySelector('[data-testid="timing-tower"]');
  if (!pill || !tower) return 'missing';
  const a = pill.getBoundingClientRect(), b = tower.getBoundingClientRect();
  const hit = !(a.right < b.left || a.left > b.right || a.bottom < b.top || a.top > b.bottom);
  return { hit, pill: [a.x|0, a.y|0, a.width|0, a.height|0], tower: [b.x|0, b.y|0, b.width|0, b.height|0] };
});
console.log('header pill overlaps tower?', JSON.stringify(overlap));
await browser.close();
