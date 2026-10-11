<!--
  The phone's bottom tab bar (ROADMAP W7): every view one thumb away, each
  target at least 44px. `icon` is an SVG path in a 24x24 box.
-->
<script lang="ts">
	interface Item {
		id: string;
		label: string;
		icon: string;
	}

	interface Props {
		items: readonly Item[];
		selected: string;
		label: string;
		onSelect: (id: string) => void;
	}

	let { items, selected, label, onSelect }: Props = $props();
</script>

<nav class="bottom-nav" aria-label={label}>
	{#each items as item (item.id)}
		<button
			type="button"
			aria-current={item.id === selected ? 'page' : undefined}
			onclick={() => onSelect(item.id)}
		>
			<svg
				viewBox="0 0 24 24"
				width="22"
				height="22"
				fill="none"
				stroke="currentColor"
				stroke-width="1.8"
				stroke-linecap="round"
				stroke-linejoin="round"
				aria-hidden="true"><path d={item.icon}></path></svg
			>
			<span>{item.label}</span>
		</button>
	{/each}
</nav>

<style>
	.bottom-nav {
		position: sticky;
		bottom: 0;
		z-index: 20;
		display: flex;
		background: var(--panel);
		border-top: 1px solid var(--rule);
		padding-bottom: env(safe-area-inset-bottom);
	}
	button {
		flex: 1;
		min-height: 56px;
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 3px;
		border: 0;
		background: none;
		color: var(--muted);
		font: 500 11px / 1 var(--font-ui);
		cursor: pointer;
	}
	button[aria-current='page'] {
		color: var(--ink);
		font-weight: 600;
		box-shadow: inset 0 2px 0 var(--accent);
	}
	button:focus-visible {
		outline: 2px solid var(--focus-ring);
		outline-offset: -2px;
	}
</style>
