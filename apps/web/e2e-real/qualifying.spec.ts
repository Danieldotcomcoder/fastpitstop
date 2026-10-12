/** Qualifying on a real replay (`just verify-real`, docs/ROADMAP.md W4):
 * the 2024 Miami qualifying from the local backfill, replayed fast through
 * the real API and frontend, must knock out the right drivers at each break
 * and place them where F1 classified them. Skips itself when the backfill is
 * absent (CI has no F1 data). */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const SESSION = path.join(REPO_ROOT, 'data/historical/season=2024/round=06/session=Qualifying');

test.skip(!fs.existsSync(SESSION), 'no 2024 Miami qualifying under data/historical');

test('2024 Miami qualifying knocks out the right cars at each break', async ({ page }) => {
	test.setTimeout(240_000);
	const problems: string[] = [];
	page.on('pageerror', (e) => problems.push(`page error: ${e.message}`));
	page.on('console', (m) => {
		if (m.type() === 'error') problems.push(`console: ${m.text()}`);
	});

	await page.goto('/sessions/2024/6?session=qualifying&speed=50');
	const board = page.getByTestId('qualifying-board');
	await expect(board.getByRole('heading', { name: 'Who makes Q2' })).toBeVisible({
		timeout: 60_000
	});
	await expect(
		page.getByLabel('Sessions this weekend').getByRole('link', { name: 'Qualifying', exact: true })
	).toHaveAttribute('aria-current', 'page');

	const outIn = (part: number) =>
		board.getByRole('list', { name: `Knocked out in part ${part}` }).getByRole('listitem');
	// Positions 16 to 20, in classification order.
	await expect(outIn(1)).toHaveText([/^16 BOT/, /^17 SAR/, /^18 RIC/, /^19 MAG/, /^20 ZHO/], {
		timeout: 120_000
	});
	await expect(board.getByRole('heading', { name: 'Who makes Q3' })).toBeVisible();
	await expect(outIn(2)).toHaveText([/^11 STR/, /^12 GAS/, /^13 OCO/, /^14 ALB/, /^15 ALO/], {
		timeout: 120_000
	});
	await expect(board.getByRole('heading', { name: 'The fight for pole' })).toBeVisible();
	await expect(page.getByTestId('quali-clock')).toContainText('Qualifying over', {
		timeout: 120_000
	});
	// Pole, from the Q3 times alone.
	await expect(board.getByRole('listitem').first()).toContainText('VER');
	expect(problems).toEqual([]);
});
