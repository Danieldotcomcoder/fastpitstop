<!--
  Radio & race control: released on the viewer's clock, newest first. A
  radio clip's transcript carries its own confidence, and a low one is
  labelled as a guess rather than shown as certain.
-->
<script lang="ts">
	import { confidenceTier } from '$lib/radio/view';
	import { colorForTeam } from '$lib/teams';
	import ConfBadge from './ConfBadge.svelte';
	import type { FeedItem } from './race-feed';

	interface Props {
		items: FeedItem[];
		filter: 'all' | 'radio' | 'rc';
		codes: Record<string, string>;
		teams: Record<string, string | null>;
		onFilter: (filter: 'all' | 'radio' | 'rc') => void;
	}

	let { items, filter, codes, teams, onFilter }: Props = $props();
	let expanded = $state(false);

	// One clip plays at a time, from a single element owned by the lane.
	let playing = $state<string | null>(null);
	let player: HTMLAudioElement | null = null;

	function toggle(id: string, url: string): void {
		if (playing === id) {
			player?.pause();
			playing = null;
			return;
		}
		player?.pause();
		player = new Audio(url);
		player.addEventListener('ended', () => (playing = null));
		player.addEventListener('error', () => (playing = null));
		void player.play().catch(() => (playing = null));
		playing = id;
	}

	$effect(() => () => player?.pause());

	const FILTERS = [
		{ id: 'all', label: 'All' },
		{ id: 'radio', label: 'Radio' },
		{ id: 'rc', label: 'Race control' }
	] as const;

	const TIER_LEVEL = { good: 'high', thin: 'medium', unreliable: 'low' } as const;

	function empty(): string {
		if (filter === 'radio') return 'No team radio released yet.';
		if (filter === 'rc') return 'No race control messages released yet.';
		return 'Nothing released yet.';
	}
</script>

<section class="panel p-radio" aria-labelledby="radioTitle" data-testid="desk-feed">
	<div class="panel-head">
		<div>
			<h2 class="panel-title" id="radioTitle">Radio &amp; race control</h2>
			<p class="panel-sub">Released on your clock</p>
		</div>
		<div class="filter" role="group" aria-label="Filter feed">
			{#each FILTERS as f (f.id)}
				<button type="button" aria-pressed={filter === f.id} onclick={() => onFilter(f.id)}
					>{f.label}</button
				>
			{/each}
		</div>
	</div>
	<div class="lane-wrap">
		<div class="lane-scroll">
			{#if items.length === 0}
				<div class="empty">{empty()}</div>
			{:else}
				<div class="radio-list" class:expanded aria-live="polite">
					{#each items as item, index (item.id)}
						{#if item.type === 'rc'}
							<div class="rc-item" class:extra={index >= 6}>
								<svg aria-hidden="true"><use href="#i-flag" /></svg>
								<div>
									<div class="rc-msg">{item.message}</div>
									<div class="rc-sub">
										Race control{item.lap !== null ? ` · lap ${item.lap}` : ''}
									</div>
								</div>
							</div>
						{:else}
							{@const clip = item.clip}
							{@const tier = confidenceTier(clip.confidence)}
							<div class="radio-item" class:extra={index >= 6}>
								<div class="radio-top">
									{#if clip.driver}
										<span class="drvchip"
											><i style:background={colorForTeam(teams[clip.driver] ?? null)}></i>{codes[
												clip.driver
											] ?? clip.driver}</span
										>
									{/if}
									{#if clip.lapNumber !== null}<span class="lapchip">Lap {clip.lapNumber}</span
										>{/if}
									{#if clip.trackStatus === 'vsc' || clip.trackStatus === 'sc'}<span
											class="tstat vsc">{clip.trackStatus.toUpperCase()}</span
										>{/if}
									<span class="spacer"></span>
									{#if tier}<ConfBadge level={TIER_LEVEL[tier]} />{/if}
									{#if clip.audioUrl}
										<button
											type="button"
											class="audio-btn"
											aria-pressed={playing === item.id}
											aria-label={playing === item.id ? 'Stop this clip' : 'Play this clip'}
											onclick={() => toggle(item.id, clip.audioUrl!)}
											><svg aria-hidden="true"><use href="#i-radio" /></svg>{playing === item.id
												? 'Stop'
												: 'Listen'}</button
										>
									{:else}
										<span class="audio"
											><svg width="12" height="12" aria-hidden="true"><use href="#i-radio" /></svg
											>Radio</span
										>
									{/if}
								</div>
								{#if clip.hasTranscript && clip.transcriptText}
									<p class="transcript" class:low={tier === 'unreliable'}>
										“{clip.transcriptText}”
									</p>
									{#if tier === 'unreliable'}
										<div class="radio-note">
											Low-confidence transcript. Treat the words as a guess.
										</div>
									{/if}
								{:else}
									<p class="transcript low">Transcript not available yet.</p>
								{/if}
							</div>
						{/if}
					{/each}
				</div>
				{#if items.length > 6}
					<button
						class="btn radio-more"
						type="button"
						aria-expanded={expanded}
						onclick={() => (expanded = !expanded)}
						>{expanded ? 'Show fewer' : `Show ${items.length - 6} earlier`}</button
					>
				{/if}
			{/if}
		</div>
	</div>
</section>
