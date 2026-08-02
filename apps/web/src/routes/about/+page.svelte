<script lang="ts">
	import { brand } from '$lib/brand';
</script>

<svelte:head>
	<title>About · {brand.name}</title>
</svelte:head>

<article class="about">
	<h1>About {brand.name}</h1>
	<p>
		{brand.name} is an unofficial Formula 1 companion. It derives pace, degradation, and
		pit-window state from timing data, and holds everything back to your broadcast delay so
		nothing spoils.
	</p>
	<p class="muted">
		This page will cover known limitations, what's still rough in the beta, and a changelog.
		Version {brand.version}.
	</p>
</article>

<style>
	.muted {
		color: var(--text-muted);
		font-size: var(--font-size-sm);
	}
</style>
