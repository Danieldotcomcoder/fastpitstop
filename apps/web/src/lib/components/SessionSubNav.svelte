<script lang="ts">
	import { SESSION_SECTIONS, type SessionSectionId } from '$lib/session-detail';

	interface Props {
		active: SessionSectionId;
		onJump?: (id: SessionSectionId) => void;
	}

	let { active, onJump }: Props = $props();

	function jump(id: SessionSectionId): void {
		onJump?.(id);
		const el = document.getElementById(id);
		el?.scrollIntoView({ behavior: 'smooth', block: 'start' });
		history.replaceState(null, '', `#${id}`);
	}
</script>

<nav class="subnav" aria-label="Session sections">
	{#each SESSION_SECTIONS as section}
		<button
			type="button"
			class="chip"
			class:active={active === section.id}
			aria-current={active === section.id ? 'true' : undefined}
			onclick={() => jump(section.id)}
		>
			<span class="num tabular" aria-hidden="true">{section.shortcut}</span>
			{section.label}
		</button>
	{/each}
</nav>

<style>
	.subnav {
		display: flex;
		gap: var(--space-1);
		overflow-x: auto;
		padding: var(--space-2) 0;
		border-bottom: 1px solid var(--hairline);
		background: color-mix(in srgb, var(--surface-base) 92%, transparent);
		scrollbar-width: thin;
		position: sticky;
		top: 0;
		z-index: 20;
	}

	.chip {
		flex: none;
		background: transparent;
		border: 1px solid transparent;
		color: var(--text-muted);
		font-size: var(--font-size-xs);
		font-weight: 500;
		padding: var(--space-1) var(--space-2);
		cursor: pointer;
		white-space: nowrap;
	}

	.chip:hover {
		color: var(--text-secondary);
		border-color: var(--hairline);
	}

	.chip.active {
		color: var(--text-primary);
		border-color: var(--hairline-strong);
		background: var(--surface-raised);
	}

	.num {
		font-family: var(--font-mono);
		font-variant-numeric: tabular-nums;
		margin-right: 0.25rem;
		opacity: 0.55;
	}
</style>
