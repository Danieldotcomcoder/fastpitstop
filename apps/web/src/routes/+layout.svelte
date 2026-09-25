<script lang="ts">
	import '$lib/styles/global.css';
	import { brand } from '$lib/brand';
	import AppHeader from '$lib/components/AppHeader.svelte';
	import Footer from '$lib/components/Footer.svelte';

	let { children } = $props();
</script>

<svelte:head>
	<title>{brand.name}</title>
	<meta name="description" content="{brand.name} — {brand.tagline}. Unofficial fan project." />
	<meta name="theme-color" content="#EEEFEA" />
</svelte:head>

<div class="app-shell">
	<AppHeader />

	<main>
		{@render children()}
	</main>

	<Footer />
</div>

<style>
	.app-shell {
		display: flex;
		flex-direction: column;
		min-height: 100dvh;
	}

	main {
		flex: 1;
		width: 100%;
		max-width: 640px;
		margin: 0 auto;
		padding: var(--space-5) var(--space-4);
	}

	/* Product dashboards opt in with `.wide-dashboard` on the page root. */
	main:has(> :global(.wide-dashboard)) {
		max-width: var(--content-max);
	}

	/* The race desk owns its own width and gutters (the design's `.app`),
	   so its sync strip can run edge to edge. */
	main:has(> :global(.desk-page)) {
		max-width: none;
		padding: 0;
	}
</style>
