<script lang="ts">
	/** Race story — the salience-ranked moments of THIS session, each with
	 * its grounded, cited explanation.
	 *
	 * Fetched in ONE request (`GET /sessions/{key}/story`), which detects
	 * anomalies once server-side and narrates the most salient handful.
	 *
	 * **Spoiler gate.** The story describes the whole race up front, so it
	 * needs the same second gate `DegradationCurves`/`BrakingOverview` use:
	 * a moment renders only once the lap it happened on has actually been
	 * completed on the already-delay-gated visible event log
	 * (`RevealTracker.isLapRevealed`). Without that, opening this tab on a
	 * replay would hand the user the ending. A moment with no lap number
	 * at all is held back until the session finishes, since there is no lap
	 * to check it against. */
	import { api, type components } from '$lib/api/client';
	import {
		EMPTY_VISIBLE_STATE,
		type DelaySyncController,
		type VisibleSessionState
	} from '$lib/delay-sync';
	import { RevealTracker } from '$lib/kernel-views';
	import { driverName, type DriverNames } from '$lib/drivers';
	import {
		anomalyKindLabel,
		buildStoryMoments,
		explanationViewFromApi,
		type ExplanationView,
		type StoryMoment
	} from '$lib/why';
	import ExplanationCard from '$lib/components/ExplanationCard.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';

	type StoryMomentOut = components['schemas']['StoryMomentOut'];

	interface Props {
		apiKey: string;
		controller: DelaySyncController;
		names?: DriverNames;
	}

	let { apiKey, controller, names = {} }: Props = $props();

	let status = $state<'loading' | 'ready' | 'error'>('loading');
	let moments = $state<StoryMoment[]>([]);
	let requestVersion = 0;

	let visible = $state<VisibleSessionState>(EMPTY_VISIBLE_STATE);
	const reveal = new RevealTracker();
	$effect(() => {
		reveal.reset();
		return controller.visibleState.subscribe((v) => (visible = v));
	});

	$effect(() => {
		const version = ++requestVersion;
		status = 'loading';
		moments = [];
		api
			.GET('/sessions/{key}/story', { params: { path: { key: apiKey } } })
			.then(({ data, error }) => {
				if (version !== requestVersion) return;
				if (error || !data) {
					status = 'error';
					return;
				}
				// A local lookup built once inside this load and then read;
				// never reactive state, so SvelteMap is not needed.
				// eslint-disable-next-line svelte/prefer-svelte-reactivity
				const byId = new Map<string, ExplanationView>();
				for (const moment of data as StoryMomentOut[]) {
					if (moment.explanation) {
						byId.set(moment.anomaly.anomaly_id, explanationViewFromApi(moment.explanation));
					}
				}
				moments = buildStoryMoments(
					(data as StoryMomentOut[]).map((m) => m.anomaly),
					byId
				);
				status = 'ready';
			})
			.catch(() => {
				if (version !== requestVersion) return;
				status = 'error';
			});
		return () => {
			requestVersion++;
		};
	});

	let revealed = $derived.by(() => {
		reveal.update(visible.events);
		return moments.filter((moment) => {
			if (moment.lapNumber === null) return reveal.state.sessionFinished;
			return moment.drivers.some((driver) => reveal.isLapRevealed(driver, moment.lapNumber!));
		});
	});
	let pending = $derived(moments.length - revealed.length);

	function who(moment: StoryMoment): string {
		return moment.drivers.map((d) => names[d] ?? driverName(d)).join(' · ');
	}
</script>

{#if status === 'loading'}
	<LoadingState label="Working out what mattered…" />
{:else if status === 'error'}
	<p class="empty-note">
		The race story could not be loaded for this session. Timing and analysis remain available.
	</p>
{:else if moments.length === 0}
	<p class="empty-note">Nothing in this session crossed the bar for an explained moment.</p>
{:else if revealed.length === 0}
	<p class="empty-note">
		The race hasn't reached its first notable moment on your playback clock yet. {moments.length}
		moment{moments.length === 1 ? '' : 's'} waiting.
	</p>
{:else}
	<ol class="story">
		{#each revealed as moment, index (moment.anomalyId)}
			<li>
				<div class="moment-head">
					<span class="rank tabular">{String(index + 1).padStart(2, '0')}</span>
					<div>
						<p class="moment-kind">{anomalyKindLabel(moment.kind)}</p>
						<p class="moment-who">
							{who(moment)}{#if moment.lapNumber !== null}
								· lap {moment.lapNumber}{/if}
						</p>
					</div>
				</div>
				{#if moment.explanation}
					<ExplanationCard explanation={moment.explanation} salience={moment.salience} />
				{:else}
					<p class="empty-note">No explanation was produced for this moment.</p>
				{/if}
			</li>
		{/each}
	</ol>
	{#if pending > 0}
		<p class="pending-note">
			{pending} later moment{pending === 1 ? '' : 's'} stay hidden until the race reaches them.
		</p>
	{/if}
{/if}

<style>
	.story {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-5);
	}

	.moment-head {
		display: flex;
		align-items: baseline;
		gap: var(--space-3);
		margin-bottom: var(--space-2);
	}

	.rank {
		color: var(--text-muted);
		font-size: var(--font-size-xs);
	}

	.moment-kind {
		margin: 0;
		color: var(--text-primary);
		font: 500 var(--font-size-sm) / 1.3 var(--font-ui);
	}

	.moment-who {
		margin: 0.15rem 0 0;
		color: var(--text-muted);
		font-size: var(--font-size-xs);
	}

	.empty-note,
	.pending-note {
		color: var(--text-secondary);
		font-size: var(--font-size-sm);
		margin: 0;
	}

	.pending-note {
		margin-top: var(--space-4);
		color: var(--text-muted);
		font-size: var(--font-size-xs);
	}
</style>
