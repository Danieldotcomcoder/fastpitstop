<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { brand } from '$lib/brand';

	const LEGAL =
		'This website is unofficial and is not associated in any way with the Formula 1 companies. F1, FORMULA ONE, FORMULA 1, FIA FORMULA ONE WORLD CHAMPIONSHIP, GRAND PRIX and related marks are trade marks of Formula One Licensing B.V.';

	const feedbackHref = $derived.by(() => {
		const params = new URLSearchParams();
		params.set('route', page.url.pathname + page.url.search);
		return `${resolve('/feedback')}?${params.toString()}`;
	});
</script>

<footer class="site-footer">
	<p class="legal">{LEGAL}</p>
	<p class="meta">
		<span class="version tabular">{brand.name} {brand.version}</span>
		<span class="sep" aria-hidden="true">·</span>
		<a class="feedback-link" href={feedbackHref}>Send feedback</a>
	</p>
</footer>

<style>
	.site-footer {
		border-top: 1px solid var(--hairline);
		padding: var(--space-5) var(--space-4);
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: var(--space-3);
		text-align: center;
	}

	.legal {
		margin: 0;
		max-width: 52rem;
		color: var(--text-muted);
		font-size: var(--font-size-xs);
		line-height: 1.45;
	}

	.meta {
		margin: 0;
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: center;
		gap: var(--space-2);
		font-size: var(--font-size-xs);
		color: var(--text-muted);
	}

	.version {
		font-family: var(--font-mono);
		font-variant-numeric: tabular-nums;
	}

	.sep {
		opacity: 0.5;
	}

	.feedback-link {
		color: var(--text-secondary);
		text-decoration: none;
		border-bottom: 1px solid var(--hairline-strong);
	}

	.feedback-link:hover {
		color: var(--text-primary);
		opacity: 1;
	}
</style>
