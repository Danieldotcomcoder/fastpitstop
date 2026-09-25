<!--
  The design's confidence badge: three bars, filled by level, with the word.
  Covers both taxonomies the kernel emits (a crossover's high/medium/low, a
  fit's good/thin/unreliable), so a thin fit never looks like a good one.
-->
<script lang="ts">
	interface Props {
		level: string | null | undefined;
		extra?: string;
	}

	let { level, extra = '' }: Props = $props();

	const LEVELS: Record<string, [number, string]> = {
		high: [3, 'High'],
		medium: [2, 'Medium'],
		low: [1, 'Low'],
		good: [3, 'Good'],
		thin: [2, 'Thin'],
		unreliable: [1, 'Unreliable']
	};

	let entry = $derived(LEVELS[level ?? ''] ?? [1, level ?? 'Unknown']);
	let text = $derived(`${entry[1]}${extra ? ` · ${extra}` : ''}`);
</script>

<span class="conf" data-level={entry[0]} title={text}>
	<span class="bars" aria-hidden="true"><b></b><b></b><b></b></span><span>{text}</span>
</span>
