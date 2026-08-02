<script lang="ts">
	/** One team-radio exchange, rendered identically wherever a clip shows up
	 * — the live/replay timeline lane (`RadioLane.svelte`) and the corpus
	 * search page (`routes/radio/+page.svelte`) both use this, so a clip
	 * never looks different depending on which surface found it. `similarity`
	 * (present only from a search result) adds the score bar; `clipId`
	 * (present only from a search result, whose `RadioClipOut.audio_url` is
	 * always `null` — see that schema's own docstring) lets a click LAZILY
	 * resolve the playable URL via `GET /radio/{clip_id}` rather than the
	 * timeline lane's case, where `audioUrl` already arrived on the envelope
	 * and playback needs no network round trip beyond the browser's own. */
	import { api } from '$lib/api/client';
	import { confidenceTier, trackStatusLabel, type RadioClipView } from '$lib/radio';
	import ConfidenceBadge from './ConfidenceBadge.svelte';

	interface Props {
		clip: RadioClipView;
		clipId?: string | null;
		similarity?: number | null;
		/** Product surfaces ship transcripts only — no audio playback. */
		allowAudio?: boolean;
	}

	let { clip, clipId = null, similarity = null, allowAudio = true }: Props = $props();

	let revealed = $state(false);
	let resolvedAudioUrl = $state<string | null>(null);
	let resolveStatus = $state<'idle' | 'loading' | 'error'>('idle');

	let playableUrl = $derived(clip.audioUrl ?? resolvedAudioUrl);
	let tier = $derived(confidenceTier(clip.confidence));

	function formatCapturedAt(iso: string | null): string {
		if (iso === null) return '—';
		const parsed = new Date(iso);
		return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleTimeString();
	}

	async function reveal(): Promise<void> {
		if (revealed) {
			revealed = false;
			return;
		}
		revealed = true;
		if (playableUrl !== null || clipId === null) return;
		resolveStatus = 'loading';
		try {
			const { data, error } = await api.GET('/radio/{clip_id}', {
				params: { path: { clip_id: clipId } }
			});
			if (error || !data?.audio_url) {
				resolveStatus = 'error';
				return;
			}
			resolvedAudioUrl = data.audio_url;
			resolveStatus = 'idle';
		} catch {
			resolveStatus = 'error';
		}
	}
</script>

<article class="clip" data-testid="radio-clip-card" data-driver={clip.driver}>
	<header class="clip-header">
		<span class="driver font-mono">{clip.driver ?? '—'}</span>
		<span class="captured-at font-mono">{formatCapturedAt(clip.capturedAt)}</span>
		{#if clip.lapNumber !== null}
			<span class="context-chip">Lap {clip.lapNumber}</span>
		{/if}
		{#if clip.position !== null}
			<span class="context-chip">P{clip.position}</span>
		{/if}
		{#if clip.trackStatus !== null}
			<span class="context-chip status-{clip.trackStatus.toLowerCase()}"
				>{trackStatusLabel(clip.trackStatus)}</span
			>
		{/if}
		{#if tier !== null}
			<ConfidenceBadge level={tier} />
		{/if}
	</header>

	{#if clip.hasTranscript}
		<p class="transcript" class:low-confidence={tier === 'unreliable'}>
			{clip.transcriptText}
		</p>
		{#if tier === 'unreliable'}
			<p class="low-confidence-note">Low-confidence transcript — may not be accurate.</p>
		{/if}
	{:else}
		<p class="transcript pending">Transcription pending.</p>
	{/if}

	<footer class="clip-footer">
		{#if allowAudio && (playableUrl !== null || clipId !== null)}
			<button type="button" class="play-button" onclick={reveal}>
				{revealed ? 'Hide audio' : 'Play'}
			</button>
		{/if}
		{#if similarity !== null}
			<span class="score" title="Search relevance score">
				<span class="score-bar" style:--score={Math.max(0, Math.min(1, similarity))}></span>
				<span class="score-label font-mono">{(similarity * 100).toFixed(0)}%</span>
			</span>
		{/if}
	</footer>

	{#if allowAudio && revealed}
		{#if resolveStatus === 'loading'}
			<p class="audio-status">Loading audio…</p>
		{:else if resolveStatus === 'error'}
			<p class="audio-status">Audio unavailable for this clip.</p>
		{:else if playableUrl !== null}
			<audio controls preload="none" src={playableUrl}>
				<track kind="captions" />
			</audio>
		{/if}
	{/if}
</article>

<style>
	.clip {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		padding: var(--space-3);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-md);
		background: var(--color-bg-elevated);
	}

	.clip-header {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		flex-wrap: wrap;
	}

	.driver {
		font-weight: 700;
	}

	.captured-at {
		color: var(--color-text-muted);
		font-size: var(--font-size-xs);
	}

	.context-chip {
		padding: 0 var(--space-2);
		border-radius: 999px;
		border: 1px solid var(--color-border-strong);
		font-size: var(--font-size-xs);
		color: var(--color-text-muted);
	}

	.status-sc,
	.status-vsc,
	.status-yellow {
		color: color-mix(in srgb, var(--color-confidence-thin) 80%, var(--color-text));
		border-color: var(--color-confidence-thin);
	}
	.status-red {
		color: color-mix(in srgb, var(--color-confidence-unreliable) 80%, var(--color-text));
		border-color: var(--color-confidence-unreliable);
	}

	.transcript {
		margin: 0;
	}

	.transcript.pending {
		color: var(--color-text-faint);
		font-style: italic;
	}

	.transcript.low-confidence {
		color: var(--color-text-muted);
	}

	.low-confidence-note {
		margin: 0;
		font-size: var(--font-size-xs);
		color: var(--color-confidence-unreliable);
	}

	.clip-footer {
		display: flex;
		align-items: center;
		gap: var(--space-3);
	}

	.play-button {
		padding: var(--space-1) var(--space-3);
		border-radius: var(--radius-md);
		border: 1px solid var(--color-border-strong);
		background: var(--color-bg-elevated);
		color: var(--color-accent-strong);
		font: inherit;
		font-weight: 600;
		font-size: var(--font-size-xs);
		cursor: pointer;
	}

	.score {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		flex: 1;
	}

	.score-bar {
		position: relative;
		flex: 1;
		max-width: 8rem;
		height: 0.375rem;
		border-radius: 999px;
		background: var(--color-border);
		overflow: hidden;
	}

	.score-bar::after {
		content: '';
		position: absolute;
		inset: 0;
		width: calc(var(--score, 0) * 100%);
		background: var(--color-accent);
	}

	.score-label {
		color: var(--color-text-muted);
		font-size: var(--font-size-xs);
	}

	.audio-status {
		margin: 0;
		font-size: var(--font-size-xs);
		color: var(--color-text-muted);
	}

	audio {
		width: 100%;
	}
</style>
