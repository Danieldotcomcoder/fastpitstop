<script lang="ts">
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { onMount } from 'svelte';

	onMount(() => {
		void goto(resolve('/live'), { replaceState: true });
	});
</script>

<p class="redirect">Opening Live…</p>

<style>
	.redirect {
		color: var(--text-muted);
		font-size: var(--font-size-sm);
	}
</style>
