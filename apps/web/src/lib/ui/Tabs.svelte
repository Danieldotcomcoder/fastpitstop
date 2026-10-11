<!--
  An ARIA tab list: arrow keys move between tabs, Home and End jump to the
  ends, and only the selected tab is in the tab order. The caller renders
  the panels, each with `id="{idPrefix}-panel-{tab.id}"`, `role="tabpanel"`
  and `aria-labelledby="{idPrefix}-tab-{tab.id}"`.
-->
<script lang="ts">
	interface Tab {
		id: string;
		label: string;
	}

	interface Props {
		tabs: readonly Tab[];
		selected: string;
		idPrefix: string;
		label: string;
		onSelect: (id: string) => void;
	}

	let { tabs, selected, idPrefix, label, onSelect }: Props = $props();

	let list: HTMLDivElement | undefined = $state();

	function move(event: KeyboardEvent, index: number): void {
		const last = tabs.length - 1;
		const next =
			event.key === 'ArrowRight'
				? index === last
					? 0
					: index + 1
				: event.key === 'ArrowLeft'
					? index === 0
						? last
						: index - 1
					: event.key === 'Home'
						? 0
						: event.key === 'End'
							? last
							: null;
		if (next === null) return;
		event.preventDefault();
		const tab = tabs[next];
		if (!tab) return;
		onSelect(tab.id);
		list?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
	}
</script>

<div class="tabs" role="tablist" aria-label={label} bind:this={list}>
	{#each tabs as tab, index (tab.id)}
		<button
			type="button"
			role="tab"
			id="{idPrefix}-tab-{tab.id}"
			aria-controls="{idPrefix}-panel-{tab.id}"
			aria-selected={tab.id === selected}
			tabindex={tab.id === selected ? 0 : -1}
			onclick={() => onSelect(tab.id)}
			onkeydown={(event) => move(event, index)}>{tab.label}</button
		>
	{/each}
</div>

<style>
	.tabs {
		display: flex;
		gap: 2px;
		border-bottom: 1px solid var(--rule);
		overflow-x: auto;
		scrollbar-width: none;
	}
	button {
		flex: none;
		min-height: 44px;
		padding: 0 var(--space-3);
		border: 0;
		background: none;
		font: 500 var(--font-size-sm) / 1 var(--font-ui);
		color: var(--muted);
		cursor: pointer;
		box-shadow: inset 0 -2px 0 transparent;
	}
	button:hover {
		color: var(--ink);
	}
	button[aria-selected='true'] {
		color: var(--ink);
		box-shadow: inset 0 -2px 0 var(--accent);
	}
	button:focus-visible {
		outline: 2px solid var(--focus-ring);
		outline-offset: -2px;
	}
	@media (prefers-reduced-motion: no-preference) {
		button {
			transition:
				color var(--duration-fast) var(--ease-out),
				box-shadow var(--duration-fast) var(--ease-out);
		}
	}
</style>
