<script lang="ts">
	import { tip } from './tooltip';

	let box: HTMLDivElement | undefined = $state();
	let left = $state(0);
	let top = $state(0);

	// Placed after it renders, so it can flip away from the viewport edge
	// the way the design's own tooltip does.
	$effect(() => {
		const current = $tip;
		if (!current || !box) return;
		const width = box.offsetWidth;
		const height = box.offsetHeight;
		let x = current.x + 14;
		let y = current.y + 14;
		if (x + width > window.innerWidth - 8) x = current.x - width - 14;
		if (y + height > window.innerHeight - 8) y = current.y - height - 14;
		left = Math.max(8, x);
		top = Math.max(8, y);
	});
</script>

<svelte:window onscroll={() => tip.set(null)} />

{#if $tip}
	<div class="tooltip" bind:this={box} style:left="{left}px" style:top="{top}px" role="tooltip">
		<div class="tt-h">{$tip.title}</div>
		{#each $tip.rows as row, index (index)}
			<div class="tt-row">
				<span class="tt-key"
					>{#if row.color}<i
							style:border-color={row.color}
							style:border-top-style={row.dashed ? 'dashed' : 'solid'}
						></i>{/if}{row.label}</span
				>
				<b>{row.value}</b>
			</div>
		{/each}
	</div>
{/if}
