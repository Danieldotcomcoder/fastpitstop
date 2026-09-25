<script lang="ts">
	/** One grounded (or honestly ungrounded) explanation — rendered
	 * identically on the live/replay timeline lane and the race story
	 * page. Expanding "Cited evidence" LAZILY fetches full detail via
	 * `GET /anomalies/{id}/explanation` when the envelope only carried
	 * lightweight citation stubs (ids + hrefs), the same telemetry/radio
	 * lesson: show the claim first, pull the sources on demand. */
	import { api } from '$lib/api/client';
	import { apiBaseUrl } from '$lib/api/config';
	import {
		FALLBACK_TEXT,
		anomalyKindLabel,
		evidenceKindLabel,
		explanationViewFromApi,
		type CitedEvidenceView,
		type ExplanationView
	} from '$lib/why';
	import ConfidenceBadge from './ConfidenceBadge.svelte';

	interface Props {
		explanation: ExplanationView;
		/** Optional salience chip for the story view (timeline omits it). */
		salience?: number | null;
	}

	let { explanation, salience = null }: Props = $props();

	let expanded = $state(false);
	let detailStatus = $state<'idle' | 'loading' | 'error'>('idle');
	let detailedEvidence = $state<CitedEvidenceView[] | null>(null);

	let evidence = $derived(detailedEvidence ?? explanation.citedEvidence);
	let isFallback = $derived(!explanation.grounded || explanation.text === FALLBACK_TEXT);

	function formatTime(iso: string | null): string {
		if (iso === null) return '—';
		const parsed = new Date(iso);
		return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleTimeString();
	}

	async function toggleEvidence(): Promise<void> {
		if (expanded) {
			expanded = false;
			return;
		}
		expanded = true;
		if (detailedEvidence !== null) return;
		// Envelope citations are stubs (no summary). Always refresh from REST
		// so the user sees the real cited sources, not just ids.
		detailStatus = 'loading';
		try {
			const { data, error } = await api.GET('/anomalies/{anomaly_id}/explanation', {
				params: { path: { anomaly_id: explanation.anomalyId } }
			});
			if (error || !data) {
				detailStatus = 'error';
				return;
			}
			detailedEvidence = explanationViewFromApi(data).citedEvidence;
			detailStatus = 'idle';
		} catch {
			detailStatus = 'error';
		}
	}

	function evidenceHref(item: CitedEvidenceView): string | null {
		if (item.href === null) return null;
		// API hrefs are absolute paths on f1_api (e.g. /radio/...,
		// /sessions/.../degradation). Point them at the API host so "Open
		// source" actually hits the cited REST resource; race-control items
		// have no dedicated route (href null) — their summary text IS the
		// evidence.
		if (item.href.startsWith('http://') || item.href.startsWith('https://')) {
			return item.href;
		}
		return `${apiBaseUrl()}${item.href.startsWith('/') ? '' : '/'}${item.href}`;
	}
</script>

<article
	class="explanation"
	class:fallback={isFallback}
	data-testid="explanation-card"
	data-anomaly-id={explanation.anomalyId}
	data-grounded={explanation.grounded ? 'true' : 'false'}
	data-kind={explanation.kind}
>
	<header class="header">
		<span class="kind">{anomalyKindLabel(explanation.kind)}</span>
		{#if explanation.lapNumber !== null}
			<span class="chip">Lap {explanation.lapNumber}</span>
		{/if}
		{#each explanation.drivers as driver (driver)}
			<span class="chip font-mono">#{driver}</span>
		{/each}
		<span class="time font-mono">{formatTime(explanation.anomalyTime)}</span>
		<ConfidenceBadge level={explanation.confidence} />
		{#if salience !== null}
			<span class="chip salience font-mono" title="Salience ranking score"
				>{(salience * 100).toFixed(0)}%</span
			>
		{/if}
	</header>

	<p class="text" class:fallback-text={isFallback}>{explanation.text}</p>
	{#if isFallback}
		<p class="fallback-note">No clear cause — weak or missing evidence, not a guess.</p>
	{/if}

	{#if explanation.grounded || evidence.length > 0}
		<button
			type="button"
			class="evidence-toggle"
			data-testid="evidence-toggle"
			onclick={toggleEvidence}
			aria-expanded={expanded}
		>
			{expanded ? 'Hide cited evidence' : 'Cited evidence'}
		</button>
	{/if}

	{#if expanded}
		{#if detailStatus === 'loading'}
			<p class="evidence-status">Loading cited sources…</p>
		{:else if detailStatus === 'error'}
			<p class="evidence-status">Could not load cited sources.</p>
		{:else if evidence.length === 0}
			<p class="evidence-status">No citations attached.</p>
		{:else}
			<ul class="evidence-list" data-testid="cited-evidence">
				{#each evidence as item (item.evidenceId)}
					<li class="evidence-item" data-evidence-kind={item.kind}>
						<span class="evidence-kind">{evidenceKindLabel(item.kind)}</span>
						{#if item.summary}
							<p class="evidence-summary">{item.summary}</p>
						{:else}
							<p class="evidence-summary faint font-mono">{item.evidenceId}</p>
						{/if}
						{#if item.confidenceLabel}
							<span class="chip">{item.confidenceLabel}</span>
						{/if}
						{#if evidenceHref(item)}
							<!-- Points at f1_api (an absolute URL on another origin), not at a
							     SvelteKit route, so resolve() does not apply. -->
							<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -->
							<a class="evidence-link" href={evidenceHref(item)} data-testid="evidence-href"
								>Open source</a
							>
						{/if}
					</li>
				{/each}
			</ul>
		{/if}
	{/if}
</article>

<style>
	.explanation {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		padding: var(--space-3);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-md);
		background: var(--color-bg-elevated);
		animation: why-in var(--duration-enter, 280ms) var(--ease-standard) both;
	}

	.explanation.fallback {
		border-style: dashed;
		background: color-mix(in srgb, var(--color-bg) 70%, var(--color-bg-elevated));
	}

	.header {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		flex-wrap: wrap;
	}

	.kind {
		font-family: var(--font-display);
		font-weight: 600;
		font-size: var(--font-size-sm);
	}

	.time {
		color: var(--color-text-muted);
		font-size: var(--font-size-xs);
	}

	.chip {
		padding: 0 var(--space-2);
		border-radius: var(--radius-sm);
		border: 1px solid var(--color-border-strong);
		font-size: var(--font-size-xs);
		color: var(--color-text-muted);
	}

	.chip.salience {
		color: var(--color-accent-strong);
		border-color: color-mix(in srgb, var(--color-accent) 40%, var(--color-border));
	}

	.text {
		margin: 0;
		font-size: var(--font-size-base);
		line-height: var(--line-height-normal);
	}

	.fallback-text {
		color: var(--color-text-muted);
		font-style: italic;
	}

	.fallback-note {
		margin: 0;
		font-size: var(--font-size-xs);
		color: var(--color-confidence-unreliable);
	}

	.evidence-toggle {
		align-self: flex-start;
		padding: var(--space-1) var(--space-3);
		border-radius: var(--radius-md);
		border: 1px solid var(--color-border-strong);
		background: var(--color-bg);
		color: var(--color-accent-strong);
		font: inherit;
		font-weight: 600;
		font-size: var(--font-size-xs);
		cursor: pointer;
	}

	.evidence-status {
		margin: 0;
		font-size: var(--font-size-xs);
		color: var(--color-text-muted);
	}

	.evidence-list {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.evidence-item {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		padding: var(--space-2) var(--space-3);
		border-left: 3px solid var(--color-accent);
		background: color-mix(in srgb, var(--color-bg) 55%, transparent);
	}

	.evidence-kind {
		font-size: var(--font-size-xs);
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--color-text-muted);
	}

	.evidence-summary {
		margin: 0;
		font-size: var(--font-size-sm);
	}

	.evidence-summary.faint {
		color: var(--color-text-faint);
		font-size: var(--font-size-xs);
	}

	.evidence-link {
		font-size: var(--font-size-xs);
		font-weight: 600;
		color: var(--color-accent-strong);
	}

	@keyframes why-in {
		from {
			opacity: 0;
			transform: translateY(0.35rem);
		}
		to {
			opacity: 1;
			transform: translateY(0);
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.explanation {
			animation: none;
		}
	}
</style>
