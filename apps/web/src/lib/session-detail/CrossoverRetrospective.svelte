<script lang="ts">
	import type { components } from '$lib/api/client';
	import { driverName, type DriverNames } from '$lib/drivers';
	import ConfidenceBadge from '$lib/components/ConfidenceBadge.svelte';
	import Estimate from '$lib/components/Estimate.svelte';
	import { formatSigned } from '$lib/estimate';
	type Crossover = components['schemas']['CrossoverResultOut'];
	let { crossovers, names = {} }: { crossovers: Crossover[]; names?: DriverNames } = $props();
	let index = $state(0);
	let activeIndex = $derived(Math.min(index, Math.max(0, crossovers.length - 1)));
	let current = $derived(crossovers[activeIndex]);
	const label = (driver: string) => names[driver] ?? driverName(driver);
	const windows = {
		open: 'The window was open',
		closed: 'The window was closed',
		opens_later: 'The opportunity came later',
		unknown: 'Not enough evidence'
	};
</script>

{#if !current}<p class="muted">No pit-stop battle has enough clean-lap data for an estimate.</p>
{:else}
	<div class="duel-nav">
		<label
			>Choose a battle<select
				aria-label="Choose strategy battle"
				onchange={(e) => (index = Number(e.currentTarget.value))}
			>
				{#each crossovers as c, i (`${c.attacker}-${c.defender}-${i}`)}<option value={i} selected={i === activeIndex}
						>Lap {c.decision_lap} · {label(c.attacker)} / {label(c.defender)} · {c.kind}</option
					>{/each}
			</select></label
		>
		<div class="paging">
			<button
				disabled={activeIndex === 0}
				onclick={() => (index = activeIndex - 1)}
				aria-label="Previous battle">←</button
			><span>{activeIndex + 1} / {crossovers.length}</span><button
				disabled={activeIndex === crossovers.length - 1}
				onclick={() => (index = activeIndex + 1)}
				aria-label="Next battle">→</button
			>
		</div>
	</div>
	{#key activeIndex}
		<article class="battle">
			<div class="battle-top">
				<span class="kind">{current.kind} · decision lap {current.decision_lap}</span
				><ConfidenceBadge level={current.confidence} />
			</div>
			<div class="duel">
				<div>
					<h3>{label(current.attacker)}</h3>
					<p>Modelled stop · lap {current.attacker_stop_lap ?? '—'}</p>
				</div>
				<span class="versus">vs</span>
				<div>
					<h3>{label(current.defender)}</h3>
					<p>Modelled stop · lap {current.defender_stop_lap ?? '—'}</p>
				</div>
			</div>
			<p class="verdict">{windows[current.window]}</p>
			<p class="muted">
				{current.kind === 'undercut'
					? 'Could stopping first gain the position after both cars had pitted?'
					: 'Could staying out longer gain the position after both cars had pitted?'}
			</p>
			<details>
				<summary>Explore the projection</summary>
				<div class="stats">
					<Estimate
						label="Margin if covered"
						value={current.projected_gap_if_covered_s}
						unit="s"
						ciLow={current.projected_gap_if_covered_low_s}
						ciHigh={current.projected_gap_if_covered_high_s}
						sampleSize={Math.min(
							current.attacker_state.n_laps_in_fit ?? 0,
							current.defender_state.n_laps_in_fit ?? 0
						)}
						method="pace projection over pit stagger"
						format={formatSigned}
					/>
					<Estimate
						label="Rejoin gap"
						value={current.projected_rejoin_gap_s}
						unit="s"
						sampleSize={Math.min(
							current.attacker_state.n_laps_in_fit ?? 0,
							current.defender_state.n_laps_in_fit ?? 0
						)}
						method="pace projection over pit stagger"
						format={formatSigned}
					/>
				</div>
				{#if current.reasons.length}<ul class="muted">
						{#each current.reasons as reason (reason)}<li>{reason}</li>{/each}
					</ul>{/if}
			</details>
		</article>
	{/key}
{/if}

<style>
	.duel-nav {
		display: flex;
		gap: 1rem;
		align-items: center;
		justify-content: space-between;
		flex-wrap: wrap;
	}
	label {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
		color: var(--text-secondary);
		font-size: 0.75rem;
		min-width: 0;
		max-width: 100%;
	}
	select {
		min-width: 0;
		max-width: 100%;
		padding: 0.7rem;
		color: var(--text-primary);
		background: var(--surface-base);
		border: 1px solid var(--hairline-strong);
		border-radius: 5px;
		font: 0.875rem var(--font-ui);
	}
	.paging {
		display: flex;
		gap: 0.8rem;
		align-items: center;
		font: 0.875rem var(--font-mono);
	}
	button {
		width: 40px;
		height: 40px;
		border: 1px solid var(--hairline-strong);
		background: var(--surface-overlay);
		color: var(--text-primary);
		border-radius: 5px;
		cursor: pointer;
	}
	button:disabled {
		opacity: 0.3;
		cursor: default;
	}
	.battle {
		background: var(--surface-base);
		border: 1px solid var(--hairline);
		border-radius: 8px;
		padding: clamp(1rem, 3vw, 2rem);
	}
	.battle-top {
		display: flex;
		gap: 1rem;
		align-items: center;
		justify-content: space-between;
		flex-wrap: wrap;
	}
	.kind {
		font: 0.75rem var(--font-mono);
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: var(--text-secondary);
	}
	.duel {
		display: grid;
		grid-template-columns: 1fr auto 1fr;
		align-items: center;
		gap: 1.5rem;
		margin: 1.8rem 0;
	}
	.duel h3 {
		font-size: clamp(1.25rem, 2.5vw, 2rem);
		margin: 0;
	}
	.duel p {
		color: var(--text-secondary);
		font-size: 0.875rem;
		margin: 0.35rem 0 0;
	}
	.versus {
		color: var(--text-muted);
		font: 0.875rem var(--font-mono);
	}
	.verdict {
		font-size: 1.25rem;
		font-weight: 500;
		margin-bottom: 0.4rem;
	}
	.muted {
		color: var(--text-secondary);
		font-size: 0.875rem;
	}
	summary {
		cursor: pointer;
		padding: 0.9rem 0;
		color: var(--text-primary);
		font-size: 0.875rem;
		border-top: 1px solid var(--hairline);
		margin-top: 1rem;
	}
	.stats {
		display: flex;
		gap: 2rem;
		flex-wrap: wrap;
		padding: 1rem 0;
	}
</style>
