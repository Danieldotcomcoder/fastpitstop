/** The timing tower against F1's official timing (`just verify-real`,
 * docs/ROADMAP.md W4: "every tower field checked against the official
 * timing for the W0 sessions"). Each race is replayed to the flag through
 * the real API and frontend; then every row the tower shows is compared
 * with the official classification and lap data
 * (`deploy/official_timing.py`, reading the backfill's results and laps):
 * place, laps completed, last lap, best lap, stops, finishing tyre, and
 * whether the car is marked out.
 *
 * Skips itself when the backfill is absent (CI has no F1 data). */
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const BACKFILL = path.join(REPO_ROOT, 'data', 'historical', 'manifest.json');

/** The W0 races (`real-sessions.spec.ts`'s REAL_SESSIONS), and two sprints:
 * a dry one and a wet one every car but one pitted in. (The 2024 Miami
 * sprint is no test: its archive gives every car two pre-race pit visits
 * as laps 1 and 2.) */
const RACES = [
	'2024-1-race',
	'2024-6-race',
	'2024-8-race',
	'2024-21-race',
	'2025-3-race',
	'2024-5-sprint',
	'2025-6-sprint'
];

interface Official {
	number: string;
	position: number | null;
	classified: boolean;
	status: string;
	laps: number | null;
	last_lap_s: number | null;
	best_lap_s: number | null;
	stops: number | null;
	compound: string | null;
}

test.skip(!fs.existsSync(BACKFILL), 'no backfill under data/historical (CI has no F1 data)');

let official: Record<string, Record<string, Official>> = {};
test.beforeAll(() => {
	const out = execFileSync(
		'uv',
		['run', 'python', '-W', 'ignore', 'deploy/official_timing.py', ...RACES],
		{ cwd: REPO_ROOT, encoding: 'utf-8' }
	);
	official = JSON.parse(out);
});

const close = (a: number | null, b: number | null) =>
	a === null || b === null ? a === b : Math.abs(a - b) < 0.0015;

for (const key of RACES) {
	test(`${key}: the tower at the flag matches the official timing`, async ({ page }) => {
		test.setTimeout(180_000);
		const [year, round, type] = key.split('-');
		const session = type === 'sprint' ? 'session=sprint&' : '';
		await page.goto(`/sessions/${year}/${round}?${session}speed=0`);
		await expect(page.locator('.lapcount .caps')).toHaveText('Finished', { timeout: 150_000 });
		// The last crossings release a moment after the session's end.
		await page.waitForTimeout(3_000);
		const rows = await page
			.getByTestId('tower-row')
			.evaluateAll((els) => els.map((el) => ({ ...(el as HTMLElement).dataset })));
		const want = official[key]!;
		const problems: string[] = [];
		for (const [code, o] of Object.entries(want)) {
			const row = rows.find((r) => r.code === code);
			if (!row) {
				if (o.laps) problems.push(`${code}: missing from the tower`);
				continue;
			}
			const num = (v: string | undefined) => (v === undefined || v === '' ? null : Number(v));
			const disqualified = /disqualified/i.test(o.status);
			// A car that never completed a lap shows none; a disqualified
			// car's official lap count is reset to zero.
			const laps = num(row.lap) ?? 0;
			const checks: [string, unknown, unknown, boolean][] = [
				['last lap', num(row.last), o.last_lap_s, close(num(row.last), o.last_lap_s)],
				['best lap', num(row.best), o.best_lap_s, close(num(row.best), o.best_lap_s)],
				['stops', num(row.stops), o.stops, num(row.stops) === o.stops],
				['tyre', row.compound || null, o.compound, (row.compound || null) === o.compound]
			];
			if (!disqualified) checks.push(['laps', laps, o.laps, laps === (o.laps ?? 0)]);
			// Out means the car stopped: lap-1 crashers included (2024
			// Monaco's four), and one classified for covering 90% of the
			// distance (the 2024 China sprint's Alonso, out on lap 18 of 19,
			// 20th). A disqualified car finished the race (the stewards'
			// call comes later), and a non-starter never ran.
			if (!disqualified && !/did not start/i.test(o.status)) {
				const out = row.retired === 'true';
				const stopped = !/^(finished|lapped|\+\d+ laps?)$/i.test(o.status);
				checks.push(['out', out, stopped, out === stopped]);
			}
			if (o.classified) {
				checks.push(['place', num(row.pos), o.position, num(row.pos) === o.position]);
			}
			for (const [field, ours, theirs, ok] of checks) {
				if (!ok) problems.push(`${code} ${field}: tower ${ours}, official ${theirs}`);
			}
		}
		expect(problems).toEqual([]);
	});
}
