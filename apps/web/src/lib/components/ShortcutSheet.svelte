<script lang="ts">
	import { SESSION_SECTIONS } from '$lib/session-detail';

	interface Props {
		open: boolean;
		onClose: () => void;
	}

	let { open, onClose }: Props = $props();
</script>

{#if open}
	<div class="sheet" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts">
		<button type="button" class="backdrop" aria-label="Close" onclick={onClose}></button>
		<div class="panel">
			<header>
				<h2>Shortcuts</h2>
				<button type="button" class="close" onclick={onClose}>Close</button>
			</header>
			<ul>
				{#each SESSION_SECTIONS as section}
					<li>
						<kbd class="tabular">{section.shortcut}</kbd>
						<span>{section.label}</span>
					</li>
				{/each}
				<li>
					<kbd class="tabular">/</kbd>
					<span>Focus radio search</span>
				</li>
				<li>
					<kbd class="tabular">?</kbd>
					<span>Open this sheet</span>
				</li>
			</ul>
		</div>
	</div>
{/if}

<style>
	.sheet {
		position: fixed;
		inset: 0;
		z-index: 40;
		display: grid;
		place-items: center;
		padding: var(--space-4);
	}

	.backdrop {
		position: absolute;
		inset: 0;
		border: none;
		background: color-mix(in srgb, var(--surface-base) 70%, transparent);
		cursor: pointer;
	}

	.panel {
		position: relative;
		width: min(24rem, 100%);
		background: var(--surface-raised);
		border: 1px solid var(--hairline-strong);
		padding: var(--space-5);
	}

	header {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		margin-bottom: var(--space-4);
	}

	header h2 {
		margin: 0;
		font-size: var(--font-size-lg);
	}

	.close {
		background: none;
		border: 1px solid var(--hairline);
		color: var(--text-secondary);
		padding: var(--space-1) var(--space-2);
		font-size: var(--font-size-xs);
		cursor: pointer;
	}

	ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	li {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		font-size: var(--font-size-sm);
		color: var(--text-secondary);
	}

	kbd {
		font-family: var(--font-mono);
		font-variant-numeric: tabular-nums;
		font-size: var(--font-size-xs);
		border: 1px solid var(--hairline-strong);
		padding: 0.15rem 0.45rem;
		min-width: 1.5rem;
		text-align: center;
		color: var(--text-primary);
	}
</style>
