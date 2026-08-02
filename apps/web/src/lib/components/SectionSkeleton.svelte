<script lang="ts">
	interface Props {
		height?: string;
		label?: string;
	}

	let { height = '12rem', label = 'Loading…' }: Props = $props();
</script>

<div class="skeleton" style="--h: {height}" aria-busy="true" aria-label={label}>
	<span class="bar"></span>
	<span class="bar short"></span>
	<span class="block"></span>
</div>

<style>
	.skeleton {
		border: 1px solid var(--hairline);
		background: var(--surface-raised);
		padding: var(--space-4);
		min-height: var(--h);
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}

	.bar,
	.block {
		display: block;
		background: var(--surface-overlay);
		border-radius: var(--radius-sm);
	}

	.bar {
		height: 0.75rem;
		width: 40%;
	}

	.bar.short {
		width: 24%;
	}

	.block {
		flex: 1;
		min-height: 6rem;
		width: 100%;
	}
</style>
