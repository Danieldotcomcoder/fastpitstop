<!--
  The site header, in the design's brand: the italic wordmark, the
  "Unofficial" tag, and the three places to go.

  It used to carry a Delay Sync pill too (pinned to the bottom of the screen
  on phones). The race desk's own sticky spoiler-shield strip is now that
  control at every width, and two controls for one clock was one too many.
-->
<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { brand } from '$lib/brand';

	const links = [
		{ href: '/live', label: 'Live' },
		{ href: '/sessions', label: 'Sessions' },
		{ href: '/about', label: 'About' }
	] as const;

	function isActive(href: string): boolean {
		const path = page.url.pathname;
		if (href === '/sessions') return path === '/sessions' || path.startsWith('/sessions/');
		return path === href || path.startsWith(`${href}/`);
	}
</script>

<header class="app-header">
	<div class="brand-block">
		<a class="wordmark" href={resolve('/live')}>{brand.name}</a>
		<a
			class="unofficial"
			href={resolve('/about')}
			title="Unofficial fan project, not associated with Formula 1">Unofficial</a
		>
	</div>
	<nav class="app-nav" aria-label="Primary">
		{#each links as link (link.href)}
			<a href={resolve(link.href)} class:active={isActive(link.href)}>{link.label}</a>
		{/each}
	</nav>
</header>

<style>
	.app-header {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 16px;
		/* A flex item in the shell's column: without a width, auto margins
		   shrink it to its content and centre the whole row. */
		width: 100%;
		max-width: 1680px;
		margin: 0 auto;
		padding: 14px 16px 10px;
		background: var(--paper);
	}

	@media (min-width: 900px) {
		.app-header {
			padding-inline: 20px;
		}
	}

	.brand-block {
		display: flex;
		align-items: baseline;
		gap: 10px;
		min-width: 0;
	}

	.wordmark {
		font: italic 600 24px/1 var(--font-display);
		letter-spacing: -0.02em;
		color: var(--ink);
		text-decoration: none;
		white-space: nowrap;
	}

	.unofficial {
		font: 500 10.5px/1 var(--font-mono);
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: var(--muted);
		border: 1px solid var(--rule-strong);
		border-radius: 3px;
		padding: 3px 5px;
		text-decoration: none;
	}

	.unofficial:hover {
		color: var(--ink-2);
		border-color: var(--ink-2);
	}

	.app-nav {
		display: flex;
		gap: 16px;
		font: 500 13px/1 var(--font-ui);
	}

	.app-nav a {
		color: var(--muted);
		text-decoration: none;
		padding-bottom: 2px;
		border-bottom: 1px solid transparent;
	}

	.app-nav a:hover {
		color: var(--ink);
	}

	.app-nav a.active {
		color: var(--ink);
		border-bottom-color: var(--ink);
	}
</style>
