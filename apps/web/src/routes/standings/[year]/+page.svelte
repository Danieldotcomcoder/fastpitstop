<script lang="ts">
	import { page } from '$app/state';
	import StandingsPage from '$lib/standings/StandingsPage.svelte';
	import { seoFor } from '$lib/seo';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
</script>

<svelte:head>
	<title>{seoFor(page.url.pathname).title}</title>
</svelte:head>

{#key data.season}
	<StandingsPage season={data.season} seasons={data.seasons} />
{/key}
