<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		title?: string;
		subtitle?: string;
		children: Snippet;
	}

	let { title, subtitle, children }: Props = $props();
</script>

<section class="panel">
	{#if title}
		<header class="panel-header">
			<h2>{title}</h2>
			{#if subtitle}
				<p class="panel-subtitle">{subtitle}</p>
			{/if}
		</header>
	{/if}
	<div class="panel-body">
		{@render children()}
	</div>
</section>

<style>
	.panel {
		background: var(--surface-raised);
		border: 1px solid var(--hairline);
		border-radius: var(--radius-md);
		padding: clamp(1rem, 2vw, 1.5rem);
	}

	.panel-header {
		margin-bottom: var(--space-4);
	}

	.panel-header h2 {
		margin-bottom: var(--space-1);
	}

	.panel-subtitle {
		margin: 0;
		color: var(--text-secondary);
		font-size: var(--font-size-sm);
	}

	.panel-body {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
	}
</style>
