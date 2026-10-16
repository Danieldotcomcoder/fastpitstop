<script lang="ts">
	/** Our calls in one race (ROADMAP W5, mockup "Ledger"): every call the
	 * desk made, settled as right, wrong or not counted, with today's score.
	 * Held back to the lap the viewer says they have watched, like the story
	 * page, and sharing its remembered lap: calls made after it, and how the
	 * waiting ones end, stay hidden. The race's own length is never shown.
	 * `?upto=` (a lap, or `all`) sets the lap from a link; `?session=` picks
	 * a sprint. */
	import '$lib/styles/desk.css';
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { api, type components } from '$lib/api/client';
	import CallCard from '$lib/calls/CallCard.svelte';
	import Scorecard from '$lib/calls/Scorecard.svelte';
	import { groupOf, HELD_OUT_RECORD, recordCell, scorecardOf, type CallOut } from '$lib/calls';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import { surname } from '$lib/desk/format';
	import { driverName } from '$lib/drivers';
	import { seoFor, toSeoSession } from '$lib/seo';
	import { storedUpTo, storeUpTo } from '$lib/why/story-view';

	type Session = components['schemas']['SessionSummaryOut'];
	type Ledger = components['schemas']['CallLedgerOut'];

	const year = $derived(Number(page.params.year));
	const round = $derived(Number(page.params.round));

	let session = $state<Session | null>(null);
	let ledger = $state<Ledger | null>(null);
	let status = $state<'loading' | 'error' | 'ready'>('loading');
	let ledgerStatus = $state<'idle' | 'loading' | 'error' | 'ready'>('idle');
	let error = $state('');
	/** The last lap watched; null for the whole race. Until the viewer says,
	 * nothing past the start shows. */
	let upTo = $state<number | null>(0);
	let request = 0;

	let eventName = $derived(session?.event_name ?? 'this race');
	const name = (driver: string) => surname(driverName(driver, null, null, year)) ?? driver;
	const team = () => null;

	let calls = $derived(ledger?.calls ?? []);
	let groups = $derived({
		counted: calls.filter((c) => groupOf(c) === 'counted'),
		waiting: calls.filter((c) => groupOf(c) === 'waiting'),
		not_counted: calls.filter((c) => groupOf(c) === 'not_counted')
	});
	let score = $derived(scorecardOf(calls));
	const GROUPS = [
		{ id: 'counted', title: 'Counted', note: 'Settled by the race, right or wrong' },
		{
			id: 'waiting',
			title: 'Waiting',
			note: 'Locked when the car boxed; the race has not settled them yet'
		},
		{
			id: 'not_counted',
			title: 'Not counted',
			note: 'Not a fair test of the call, or too little to call: never scored either way'
		}
	] as const;
	const KIND = { undercut: 'Undercut', rejoin: 'Rejoin' } as const;

	async function loadSession(): Promise<void> {
		status = 'loading';
		try {
			const { data } = await api.GET('/sessions');
			const wanted = page.url.searchParams.get('session') ?? 'race';
			const matches = (data ?? []).filter(
				(s) => s.session_key.year === year && s.session_key.round === round
			);
			session =
				matches.find((s) => s.session_key.session_type === wanted) ??
				matches.find((s) => s.session_key.session_type === 'race') ??
				null;
			if (!session) {
				error = 'This session is not available.';
				status = 'error';
				return;
			}
			status = 'ready';
			const raw = page.url.searchParams.get('upto');
			const linked =
				raw === 'all'
					? null
					: raw !== null && Number.isFinite(Number(raw))
						? Number(raw)
						: undefined;
			const remembered = storedUpTo(session.key);
			upTo = linked !== undefined ? linked : remembered === undefined ? 0 : remembered;
			await loadLedger();
		} catch {
			error = 'Could not reach the API.';
			status = 'error';
		}
	}

	async function loadLedger(): Promise<void> {
		if (!session) return;
		const own = ++request;
		ledgerStatus = 'loading';
		try {
			const { data, error: failed } = await api.GET('/sessions/{key}/calls', {
				params: {
					path: { key: session.key },
					query: upTo === null ? {} : { upto: upTo }
				}
			});
			if (own !== request) return;
			if (failed || !data) {
				ledgerStatus = 'error';
				return;
			}
			ledger = data;
			ledgerStatus = 'ready';
		} catch {
			if (own === request) ledgerStatus = 'error';
		}
	}

	function setUpTo(next: number | null): void {
		upTo = next === null ? null : Math.max(0, Math.round(next));
		if (session) storeUpTo(session.key, upTo);
		const type = session?.session_key.session_type;
		const which = type && type !== 'race' ? `session=${type}&` : '';
		const lap = upTo === null ? 'all' : String(upTo);
		void goto(resolve(`/sessions/${year}/${round}/calls?${which}upto=${lap}`), {
			replaceState: true,
			noScroll: true,
			keepFocus: true
		});
		void loadLedger();
	}

	onMount(() => void loadSession());

	let deskHref = $derived.by(() => {
		const type = session?.session_key.session_type;
		const base = resolve(`/sessions/${year}/${round}`);
		return type && type !== 'race' ? `${base}?session=${type}` : base;
	});
</script>

<svelte:head
	><title>{seoFor(page.url.pathname, session ? [toSeoSession(session)] : []).title}</title
	></svelte:head
>

<!-- Under `.desk-page` for the desk's confidence badges, so a call reads the same
     here as on the race desk. -->
<div class="ledger-page desk-page">
	<header>
		<p class="kicker">
			{year} · Round {round} · {session?.session_key.session_type.replace('_', ' ') ?? 'race'}
		</p>
		<h1 data-testid="ledger-heading">
			{#if upTo === null}Our calls in the {eventName}{:else if upTo === 0}Our calls in the {eventName},
				held back to the start{:else}Our calls in the {eventName}, to lap {upTo}{/if}
		</h1>
		<p class="lede">
			{#if upTo === null}
				Every call we made, and how the race settled it.
			{:else}
				Held back to {upTo === 0 ? 'the start' : `lap ${upTo}`}, where you said you are. Calls made
				after it, and how the waiting ones end, stay hidden until you move on.
			{/if}
		</p>
		<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -->
		<p class="back"><a href={deskHref}>← Back to the race</a></p>
	</header>

	{#if status === 'loading'}
		<LoadingState label="Opening the race…" />
	{:else if status === 'error'}
		<p class="none">{error}</p>
	{:else}
		<div class="upto" role="group" aria-label="How far you have watched">
			<label for="upto-lap">I have watched up to lap</label>
			<div class="stepper">
				<button
					type="button"
					aria-label="One lap back"
					disabled={upTo === 0}
					onclick={() => setUpTo((upTo ?? 1) - 1)}>−</button
				>
				<input
					id="upto-lap"
					type="number"
					min="0"
					inputmode="numeric"
					value={upTo ?? ''}
					placeholder="all"
					data-testid="ledger-upto"
					onchange={(e) => {
						const v = (e.currentTarget as HTMLInputElement).value;
						setUpTo(v === '' ? null : Number(v));
					}}
				/>
				<button
					type="button"
					aria-label="One lap on"
					onclick={() => setUpTo(upTo === null ? null : upTo + 1)}>+</button
				>
			</div>
			<button type="button" class="all" aria-pressed={upTo === null} onclick={() => setUpTo(null)}
				>The whole race</button
			>
			<p class="hint">The race's length is not shown: it would hint at what is coming.</p>
		</div>

		{#if ledgerStatus === 'loading' && !ledger}
			<LoadingState label="Settling the calls…" />
		{:else if ledgerStatus === 'error'}
			<p class="none">Could not load the calls for this race.</p>
		{:else if ledger && ledger.kinds.length === 0}
			<section class="card" data-testid="ledger-not-cleared">
				<h2>No call type is cleared yet</h2>
				<p>
					A kind of call is shown and counted here only once, on races the model never saw, its
					high-confidence calls beat its medium ones and its medium calls beat its low ones. None
					has yet, so there is nothing to score in this race.
				</p>
				<table class="record">
					<caption
						>On {HELD_OUT_RECORD.undercut.races} races of {HELD_OUT_RECORD.undercut.season} the model
						never saw</caption
					>
					<thead>
						<tr
							><th scope="col">Call</th><th scope="col">High</th><th scope="col">Medium</th><th
								scope="col">Low</th
							></tr
						>
					</thead>
					<tbody>
						{#each Object.values(HELD_OUT_RECORD) as record (record.kind)}
							<tr>
								<th scope="row">{KIND[record.kind]}</th>
								{#each record.lines as line (line.confidence)}<td>{recordCell(line)}</td>{/each}
							</tr>
						{/each}
					</tbody>
				</table>
			</section>
		{:else if ledger}
			<section class="card" aria-label="This race's score">
				<h2>
					{upTo === null ? 'This race' : upTo === 0 ? 'At the start' : `This race, to lap ${upTo}`}
				</h2>
				<Scorecard scorecard={score} kinds={ledger.kinds} final={ledger.final} />
			</section>

			{#each GROUPS as group (group.id)}
				{@const list: CallOut[] = groups[group.id]}
				<section class="group" aria-labelledby="g-{group.id}" data-testid="ledger-{group.id}">
					<h2 id="g-{group.id}">
						{group.title} <span class="count">{list.length}</span>
						<span class="note">{group.note}</span>
					</h2>
					{#if list.length === 0}
						<p class="none">None {upTo === null ? 'in this race' : 'so far'}.</p>
					{:else}
						<ol>
							{#each list as call (call.call_id)}
								<li><CallCard {call} {name} {team} /></li>
							{/each}
						</ol>
					{/if}
				</section>
			{/each}

			<section class="card how">
				<h2>How a call is scored</h2>
				<p>
					The call that counts is the one on screen the lap before the car boxed, made from the laps
					run by then and nothing after.
				</p>
				<p>
					An undercut call is right when the car that boxed comes out ahead, or behind, once both
					cars have stopped, as we said. A rejoin call is right when the car directly ahead after
					the out-lap is the one we named.
				</p>
				<p>
					A safety car between the stops, or another car pitting around the rejoin point, makes it
					not a fair test: it is listed, not scored.
				</p>
				<p>
					The same rules score the published backtest, so this page and those figures can be
					compared directly.
				</p>
			</section>
		{/if}
	{/if}
</div>

<style>
	.ledger-page {
		display: flex;
		flex-direction: column;
		gap: var(--space-5);
		max-width: 820px;
	}
	.kicker {
		margin: 0;
		font: 500 var(--font-size-xs) / 1.3 var(--font-mono);
		letter-spacing: 0.1em;
		text-transform: uppercase;
		color: var(--muted);
	}
	h1 {
		margin: var(--space-2) 0 0;
	}
	.lede {
		margin: var(--space-2) 0 0;
		color: var(--text-secondary);
	}
	.back {
		margin: var(--space-2) 0 0;
		font-size: var(--font-size-sm);
	}
	.upto {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2) var(--space-3);
		padding: var(--space-4);
		border: 1px solid var(--rule);
		border-radius: var(--radius-lg);
		background: var(--panel);
		font-size: var(--font-size-sm);
		color: var(--ink-2);
	}
	.hint {
		flex-basis: 100%;
		margin: 0;
		font-size: var(--font-size-xs);
		color: var(--muted);
	}
	input {
		min-height: 44px;
		width: 4.5rem;
		padding: 0 var(--space-3);
		border-radius: var(--radius-md);
		border: 1px solid var(--rule-strong);
		background: var(--paper);
		color: var(--ink);
		font: inherit;
		font-family: var(--font-mono);
		text-align: center;
	}
	.stepper {
		display: flex;
		gap: var(--space-1);
	}
	.stepper button,
	.all {
		min-width: 44px;
		min-height: 44px;
		border-radius: var(--radius-md);
		border: 1px solid var(--rule-strong);
		background: var(--panel);
		color: var(--ink);
		font: inherit;
		cursor: pointer;
	}
	.all {
		padding: 0 var(--space-4);
	}
	.all[aria-pressed='true'] {
		background: var(--ink);
		color: var(--paper);
	}
	.card {
		padding: var(--space-4) var(--space-5);
		border: 1px solid var(--rule);
		border-radius: var(--radius-lg);
		background: var(--panel);
	}
	.card h2,
	.group h2 {
		margin: 0;
		font: 500 var(--font-size-lg) / 1.2 var(--font-display);
	}
	.group h2 {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: var(--space-2) var(--space-3);
	}
	.count {
		font-family: var(--font-mono);
		color: var(--muted);
	}
	.note {
		font: 400 var(--font-size-sm) / 1.3 var(--font-ui);
		color: var(--muted);
	}
	.group ol {
		list-style: none;
		margin: var(--space-2) 0 0;
		padding: 0;
	}
	.none {
		margin: var(--space-2) 0 0;
		color: var(--muted);
	}
	.how p {
		margin: var(--space-2) 0 0;
		color: var(--ink-2);
	}
	.record {
		margin-top: var(--space-3);
		border-collapse: collapse;
		font-size: var(--font-size-sm);
	}
	.record caption {
		text-align: left;
		padding-bottom: var(--space-2);
		color: var(--muted);
	}
	.record th,
	.record td {
		padding: 4px 16px 4px 0;
		text-align: left;
		font-weight: 400;
	}
	.record td {
		font-family: var(--font-mono);
		font-variant-numeric: tabular-nums;
	}
</style>
