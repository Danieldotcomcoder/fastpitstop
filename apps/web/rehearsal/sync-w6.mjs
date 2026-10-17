// W6 rehearsal driver (docs/ROADMAP.md W6, "Rehearsal"): pause/resume and
// catch-up on a recorded race played as live through `deploy/rehearse_live.py`.
// Expects the rehearsal API tunnelled to 127.0.0.1:18000 and `vite dev` on
// 4340 pointed at it; BASE names the live session's replay route.
//   node apps/web/rehearsal/sync-w6.mjs
import { chromium } from '@playwright/test';
const BASE = process.env.REHEARSAL_URL ?? 'http://127.0.0.1:4340/sessions/2026/17';
const log = (step, data) =>
	console.log(JSON.stringify({ t: new Date().toISOString(), step, ...data }));
const lapOf = async (p) => Number((await p.getByTestId('session-lap').textContent()) ?? 0);
const events = async (p) =>
	Number((await p.getByTestId('session-visible-events').textContent()) ?? 0);
const lastEvent = async (p) => (await p.getByTestId('session-last-event').textContent()) ?? '';
const delayText = async (p) => (await p.getByTestId('shield-delay').textContent()) ?? '';
const numericIntervals = async (p) =>
	(await p.getByTestId('tower-row').allTextContents()).filter((t) => /\+\d+\.\d{3}/.test(t)).length;
function delaySeconds(text) {
	let m = /^([\d.]+) s$/.exec(text);
	if (m) return Number(m[1]);
	m = /^(\d+) min (\d+) s$/.exec(text);
	if (m) return Number(m[1]) * 60 + Number(m[2]);
	m = /^(\d+) h (\d+) min$/.exec(text);
	if (m) return Number(m[1]) * 3600 + Number(m[2]) * 60;
	return NaN;
}
async function spoilerCheck(p, label) {
	// Every released event is at or before the viewer's clock (device clock corrected to the server).
	const r = await p.evaluate(() => ({
		last: document.querySelector('[data-testid=session-last-event]')?.textContent ?? '',
		offset: Number(document.querySelector('[data-testid=session-clock-offset]')?.textContent ?? 0)
	}));
	const delay = delaySeconds(await delayText(p));
	const clock = Date.now() + r.offset - delay * 1000;
	const ahead = r.last ? (Date.parse(r.last) - clock) / 1000 : null;
	log('spoiler-check', {
		label,
		lastEvent: r.last,
		delay,
		aheadOfClockS: ahead,
		ok: ahead === null || ahead <= 1
	});
	return ahead === null || ahead <= 1;
}
const failures = [];
const expect = (ok, what) => {
	if (!ok) failures.push(what);
	log(ok ? 'pass' : 'FAIL', { what });
};

const browser = await chromium.launch();
const viewer = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await viewer.goto(BASE);
await viewer.getByTestId('session-lap').waitFor({ timeout: 120000 });
await viewer.waitForFunction(
	() => Number(document.querySelector('[data-testid=session-lap]')?.textContent) >= 3,
	null,
	{ timeout: 900000 }
);
log('viewer-joined', { lap: await lapOf(viewer) });

// --- pause and resume ------------------------------------------------------
// Pause only once the race so far has arrived: until then, releasing the
// backlog (all of it older than the clock) is correct, not a leak.
const offset = async () =>
	Number((await viewer.getByTestId('session-clock-offset').textContent()) ?? 0);
const serverNow = async () => Date.now() + (await offset());
await viewer.waitForFunction(
	() => {
		const last = document.querySelector('[data-testid=session-last-event]')?.textContent;
		const off = Number(
			document.querySelector('[data-testid=session-clock-offset]')?.textContent ?? 0
		);
		return !!last && Date.now() + off - Date.parse(last) < 8000;
	},
	null,
	{ timeout: 300000 }
);
log('caught-up', { lap: await lapOf(viewer), last: await lastEvent(viewer) });
const pauseAt = await serverNow();
await viewer.getByTestId('sync-pause').click();
await viewer.waitForTimeout(700);
expect(
	(await viewer.getByTestId('sync-quality').textContent()) === 'Paused',
	'quality says Paused'
);
const paused = {
	lap: await lapOf(viewer),
	events: await events(viewer),
	clock: await viewer.getByTestId('sync-clock').textContent()
};
log('paused', paused);
let leak = false;
for (let i = 0; i < 15; i++) {
	await viewer.waitForTimeout(2000);
	const last = await lastEvent(viewer);
	if (last && Date.parse(last) > pauseAt + 1000) leak = true;
}
const during = {
	lap: await lapOf(viewer),
	events: await events(viewer),
	last: await lastEvent(viewer),
	clock: await viewer.getByTestId('sync-clock').textContent()
};
log('after-30s-paused', { ...during, pauseAt: new Date(pauseAt).toISOString() });
expect(!leak, 'nothing past the moment of the pause is released while paused');
expect(
	during.lap === paused.lap && during.clock === paused.clock,
	'the lap and the clock stand still while paused'
);
await viewer.getByTestId('sync-pause').click();
const resumedDelay = delaySeconds(await delayText(viewer));
log('resumed', { delay: resumedDelay });
expect(resumedDelay >= 29 && resumedDelay <= 40, 'delay grew by the ~30 s pause');
expect(
	await spoilerCheck(viewer, 'right after resume'),
	'no event ahead of the clock after resume'
);
await viewer.waitForFunction(
	(l) => Number(document.querySelector('[data-testid=session-lap]')?.textContent) > l,
	paused.lap,
	{ timeout: 300000 }
);
log('carried-on', { lap: await lapOf(viewer) });
expect(
	await spoilerCheck(viewer, 'after carrying on'),
	'no event ahead of the clock after carrying on'
);

// --- catch-up ----------------------------------------------------------------
await viewer.waitForFunction(
	() => Number(document.querySelector('[data-testid=session-lap]')?.textContent) >= 12,
	null,
	{ timeout: 900000 }
);
const late = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await late.goto(BASE);
await late.waitForFunction(
	() => Number(document.querySelector('[data-testid=session-lap]')?.textContent) >= 12,
	null,
	{ timeout: 300000 }
);
const liveLap = await lapOf(late);
log('late-viewer-at', { lap: liveLap, intervals: await numericIntervals(late) });
await late.getByTestId('sync-catch-up').click();
await late.waitForFunction(
	() => Number(document.querySelector('[data-testid=session-lap]')?.textContent) <= 2,
	null,
	{ timeout: 60000 }
);
log('catch-up-rebuilt', {
	lap: await lapOf(late),
	delay: await delayText(late),
	quality: await late.getByTestId('sync-quality').textContent()
});
expect(
	(await late.getByTestId('sync-quality').textContent()) === 'In sync',
	'catch-up quality In sync'
);
expect(delaySeconds(await delayText(late)) > 120, 'catch-up runs beyond the slider');
expect(await spoilerCheck(late, 'catch-up start'), 'no event ahead of the clock at catch-up start');
await late.waitForFunction(
	() => Number(document.querySelector('[data-testid=session-lap]')?.textContent) >= 4,
	null,
	{ timeout: 600000 }
);
const early = { lap: await lapOf(late), intervals: await numericIntervals(late) };
log('catch-up-early-laps', early);
expect(early.lap < liveLap, 'catch-up is behind the live race');
expect(early.intervals >= 12, 'full gap history: intervals shown in the early laps');
expect(await spoilerCheck(late, 'catch-up early laps'), 'no event ahead of the clock in catch-up');
log('done', { failures });
await browser.close();
process.exit(failures.length ? 1 : 0);
