<script lang="ts">
	interface Props {
		label?: string;
	}

	let { label = 'Loading…' }: Props = $props();
</script>

<div class="loading" role="status" aria-live="polite">
	<span class="spinner" aria-hidden="true"></span>
	<span>{label}</span>
</div>

<style>
	.loading {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		color: var(--color-text-muted);
		font-size: var(--font-size-sm);
		padding: var(--space-4) 0;
	}

	.spinner {
		width: 1rem;
		height: 1rem;
		flex: none;
		border-radius: 50%;
		border: 2px solid var(--color-border-strong);
		border-top-color: var(--color-accent);
		animation: spin 800ms linear infinite;
	}

	@media (prefers-reduced-motion: reduce) {
		.spinner {
			animation-duration: 2.4s;
		}
	}

	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}
</style>
