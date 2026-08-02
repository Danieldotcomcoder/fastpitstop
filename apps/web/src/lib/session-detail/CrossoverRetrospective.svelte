<script lang="ts">
	/** The undercut/overcut retrospective — `/analysis`'s `crossovers`
	 * (`f1_kernel.undercut`'s pace-projection model, run once over the whole
	 * completed session). Worded deliberately as "the model's read of this
	 * stop", not "predicted in advance": unlike `UndercutCountdown`'s live
	 * per-lap snapshot (refit walk-forward on a slice, per CLAUDE.md's own
	 * undercut-eval discipline), this report is fit with the benefit of the
	 * full race's pace data, so presenting it as a blind foresight call would
	 * overclaim. Same `Estimate`/`ConfidenceBadge` grammar as the live
	 * countdown so the two views never look like two different products. */
	import type { components } from '$lib/api/client';
	import { formatSigned } from '$lib/estimate';
	import ConfidenceBadge from '$lib/components/ConfidenceBadge.svelte';
	import Estimate from '$lib/components/Estimate.svelte';

	type Crossover = components['schemas']['CrossoverResultOut'];

	interface Props {
		crossovers: Crossover[];
	}

	let { crossovers }: Props = $props();

	function windowLabel(c: Crossover): string {
		switch (c.window) {
			case 'open':
				return 'window was open';
			case 'closed':
				return 'window had closed';
			case 'opens_later':
				return 'window opened later';
			default:
				return 'window unclear';
		}
	}

	function fitSamples(c: Crossover): number {
		const a = c.attacker_state.n_laps_in_fit ?? 0;
		const b = c.defender_state.n_laps_in_fit ?? 0;
		return Math.min(a, b);
	}
</script>

{#if crossovers.length === 0}
	<p class="muted">
		No pit-window duel cleared the kernel's confidence bar for this race — either a quiet
		strategy race, or too few clean laps around the stops to fit.
	</p>
{:else}
	<ul class="crossover-list">
		{#each crossovers as c (`${c.kind}:${c.attacker}:${c.defender}:${c.decision_lap}`)}
			<li class="crossover-card">
				<header>
					<span class="battle font-mono">{c.attacker} vs {c.defender}</span>
					<span class="kind">{c.kind}</span>
					<ConfidenceBadge level={c.confidence} />
				</header>
				<p class="lede">
					At the decision lap ({c.decision_lap}), the {windowLabel(c)} — {c.attacker} stopped lap
					{c.attacker_stop_lap ?? '—'}, {c.defender} stopped lap {c.defender_stop_lap ?? '—'}.
				</p>
				<div class="stats-row">
					<Estimate
						label="Margin if covered"
						value={c.projected_gap_if_covered_s}
						unit="s"
						ciLow={c.projected_gap_if_covered_low_s}
						ciHigh={c.projected_gap_if_covered_high_s}
						sampleSize={fitSamples(c)}
						method="pace projection over pit stagger"
						format={formatSigned}
					/>
					<Estimate
						label="Rejoin gap"
						value={c.projected_rejoin_gap_s}
						unit="s"
						sampleSize={fitSamples(c)}
						method="pace projection over pit stagger"
						format={formatSigned}
					/>
				</div>
				{#if c.reasons.length > 0}
					<ul class="reasons">
						{#each c.reasons as reason (reason)}
							<li>{reason}</li>
						{/each}
					</ul>
				{/if}
			</li>
		{/each}
	</ul>
{/if}

<style>
	.crossover-list {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
	}

	.crossover-card {
		border: 1px solid var(--hairline);
		border-radius: var(--radius-md);
		padding: var(--space-4);
		background: var(--surface-overlay);
	}

	.crossover-card header {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		margin-bottom: var(--space-2);
		flex-wrap: wrap;
	}

	.battle {
		font-weight: 700;
		color: var(--text-primary);
	}

	.kind {
		font-size: var(--font-size-xs);
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
	}

	.lede {
		margin: 0 0 var(--space-3);
		font-size: var(--font-size-sm);
		color: var(--text-secondary);
	}

	.stats-row {
		display: flex;
		gap: var(--space-6);
		flex-wrap: wrap;
	}

	.reasons {
		margin: var(--space-3) 0 0;
		padding-left: var(--space-5);
		font-size: var(--font-size-xs);
		color: var(--text-muted);
	}

	.muted {
		color: var(--text-muted);
		font-size: var(--font-size-sm);
	}
</style>
