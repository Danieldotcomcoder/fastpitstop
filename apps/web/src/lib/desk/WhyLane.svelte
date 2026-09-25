<!--
  Why it happened: one grounded sentence per notable moment, with its
  sources. Fed only by `explanation` envelopes Delay Sync has released
  (each paced on its own anomaly time), so a moment is never explained
  before the viewer's clock reaches it. Cited evidence is fetched lazily,
  only when a card is opened.
-->
<script lang="ts">
	import { api } from '$lib/api/client';
	import { colorForTeam } from '$lib/teams';
	import {
		anomalyKindLabel,
		evidenceKindLabel,
		explanationViewFromApi,
		type CitedEvidenceView,
		type ExplanationView
	} from '$lib/why/view';
	import ConfBadge from './ConfBadge.svelte';

	interface Props {
		explanations: readonly ExplanationView[];
		codes: Record<string, string>;
		teams: Record<string, string | null>;
	}

	let { explanations, codes, teams }: Props = $props();

	/** Newest first, as the design reads: the latest moment on top. */
	let items = $derived([...explanations].reverse());
	let expanded = $state(false);

	// Cards that arrive after the first paint flash once, like the design.
	// Plain bookkeeping, not reactive state: it only records what was shown.
	const seen: Record<string, true> = {};
	let firstPaint = true;
	let fresh = $derived.by(() => {
		const out: Record<string, true> = {};
		for (const item of items) {
			if (!firstPaint && !seen[item.anomalyId]) out[item.anomalyId] = true;
			seen[item.anomalyId] = true;
		}
		firstPaint = items.length === 0 ? firstPaint : false;
		return out;
	});

	let details = $state<Record<string, CitedEvidenceView[] | 'loading' | 'error'>>({});

	async function loadEvidence(item: ExplanationView): Promise<void> {
		if (details[item.anomalyId] !== undefined) return;
		details[item.anomalyId] = 'loading';
		try {
			const { data, error } = await api.GET('/anomalies/{anomaly_id}/explanation', {
				params: { path: { anomaly_id: item.anomalyId } }
			});
			details[item.anomalyId] =
				error || !data ? 'error' : explanationViewFromApi(data).citedEvidence;
		} catch {
			details[item.anomalyId] = 'error';
		}
	}

	const ICON: Record<string, string> = {
		radio_clip: 'i-radio',
		race_control: 'i-flag',
		anomaly_fact: 'i-kernel',
		lap_context: 'i-lap',
		degradation_fit: 'i-fit'
	};

	function source(item: CitedEvidenceView): string {
		const who = item.drivers.map((d) => codes[d] ?? d).join(', ');
		return `${evidenceKindLabel(item.kind)}${who ? ` · ${who}` : ''}`;
	}
</script>

<section class="panel p-why" aria-labelledby="whyTitle" data-testid="desk-why">
	<div class="panel-head">
		<div>
			<h2 class="panel-title" id="whyTitle">Why it happened</h2>
			<p class="panel-sub">One grounded sentence per moment, with its sources</p>
		</div>
	</div>
	<div class="lane-wrap">
		<div class="lane-scroll">
			{#if items.length === 0}
				<div class="empty">Nothing yet. Explanations arrive as your clock reaches each moment.</div>
			{:else}
				<div class="why-list" class:expanded aria-live="polite">
					{#each items as item, index (item.anomalyId)}
						{@const cited = details[item.anomalyId]}
						{@const evidence = Array.isArray(cited) ? cited : item.citedEvidence}
						<article
							class="why-card"
							class:fallback={!item.grounded}
							class:fresh={fresh[item.anomalyId] === true}
							class:extra={index >= 3}
							data-testid="explanation-card"
							data-grounded={item.grounded ? 'true' : 'false'}
						>
							<div class="why-meta">
								<div class="left">
									<span class="kind">{anomalyKindLabel(item.kind)}</span>
									{#if item.lapNumber !== null}<span class="lapchip">Lap {item.lapNumber}</span
										>{/if}
									{#each item.drivers as driver (driver)}
										<span class="drvchip"
											><i style:background={colorForTeam(teams[driver] ?? null)}></i>{codes[
												driver
											] ?? driver}</span
										>
									{/each}
								</div>
								<ConfBadge level={item.confidence} />
							</div>
							<p class="why-text">{item.text}</p>
							{#if item.grounded || evidence.length > 0}
								<details
									ontoggle={(e) =>
										(e.currentTarget as HTMLDetailsElement).open && loadEvidence(item)}
								>
									<summary data-testid="evidence-toggle"
										>{item.grounded ? 'Cited evidence' : 'Evidence considered'} ({evidence.length})</summary
									>
									{#if cited === 'loading'}
										<p class="panel-foot">Loading sources…</p>
									{:else if cited === 'error'}
										<p class="panel-foot">Sources unavailable right now.</p>
									{/if}
									<ul class="evidence" data-testid="cited-evidence">
										{#each evidence as ev (ev.evidenceId)}
											<li data-evidence-kind={ev.kind}>
												<svg aria-hidden="true"><use href="#{ICON[ev.kind] ?? 'i-kernel'}" /></svg>
												<span
													><span class="src">{source(ev)}</span>{#if ev.summary}<br
														/>{ev.summary}{/if}</span
												>
												<span class="confv">{ev.confidenceLabel ?? '—'}</span>
											</li>
										{/each}
									</ul>
								</details>
							{/if}
						</article>
					{/each}
				</div>
				{#if items.length > 3}
					<button
						class="btn why-more"
						type="button"
						aria-expanded={expanded}
						onclick={() => (expanded = !expanded)}
						>{expanded ? 'Show fewer' : `Show ${items.length - 3} earlier`}</button
					>
				{/if}
			{/if}
		</div>
	</div>
</section>
