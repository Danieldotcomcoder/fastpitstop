<!--
  The site header, in the design's brand: the italic wordmark, the
  "Unofficial" tag, and the places to go.

  It used to carry a Delay Sync pill too (pinned to the bottom of the screen
  on phones). The race desk's own sticky spoiler-shield strip is now that
  control at every width, and two controls for one clock was one too many.

  The theme switch: dark is the default, and choosing light is remembered on
  this device (app.html restores it before first paint).
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { brand } from '$lib/brand';

	const links = [
		{ href: '/live', label: 'Live' },
		{ href: '/sessions', label: 'Sessions' },
		{ href: '/standings', label: 'Standings' },
		{ href: '/circuits', label: 'Circuits' },
		{ href: '/about', label: 'About' }
	] as const;

	let light = $state(false);
	onMount(() => {
		light = document.documentElement.dataset.theme === 'light';
	});

	function toggleTheme(): void {
		light = !light;
		const root = document.documentElement;
		if (light) root.dataset.theme = 'light';
		else delete root.dataset.theme;
		document
			.querySelector('meta[name="theme-color"]')
			?.setAttribute('content', light ? '#eeefea' : '#050518');
		try {
			if (light) localStorage.setItem('theme', 'light');
			else localStorage.removeItem('theme');
		} catch {
			// Storage blocked: the choice lasts until the page is closed.
		}
	}

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
	<div class="end">
		<nav class="app-nav" aria-label="Primary">
			{#each links as link (link.href)}
				<a href={resolve(link.href)} class:active={isActive(link.href)}>{link.label}</a>
			{/each}
		</nav>
		<button
			type="button"
			class="theme-switch"
			onclick={toggleTheme}
			aria-label={light ? 'Switch to dark theme' : 'Switch to light theme'}
			title={light ? 'Dark theme' : 'Light theme'}
		>
			<svg
				viewBox="0 0 24 24"
				width="16"
				height="16"
				fill="none"
				stroke="currentColor"
				stroke-width="1.8"
				stroke-linecap="round"
				stroke-linejoin="round"
				aria-hidden="true"
			>
				{#if light}
					<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"></path>
				{:else}
					<circle cx="12" cy="12" r="4"></circle>
					<path
						d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"
					></path>
				{/if}
			</svg>
		</button>
	</div>
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

	/* A phone has no room for the wordmark, the tag, every link and the
	   theme switch in one row: the links and the switch take a row of their
	   own under the wordmark, scrolling sideways if they ever outgrow it. */
	@media (max-width: 599px) {
		.app-header {
			flex-wrap: wrap;
			row-gap: 10px;
		}

		.end {
			width: 100%;
			justify-content: space-between;
		}

		.app-nav {
			gap: 14px;
			overflow-x: auto;
			scrollbar-width: none;
			padding-block: 4px;
		}
	}

	.wordmark {
		font: italic 700 24px/1 var(--font-display);
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

	.end {
		display: flex;
		align-items: center;
		gap: 12px;
	}

	.app-nav {
		display: flex;
		gap: 16px;
		font: 500 13px/1 var(--font-ui);
	}

	.theme-switch {
		display: grid;
		place-items: center;
		width: 32px;
		height: 32px;
		margin: -8px -8px -8px 0;
		border: 0;
		border-radius: 50%;
		background: none;
		color: var(--muted);
		cursor: pointer;
	}

	.theme-switch:hover {
		color: var(--ink);
		background: var(--panel-sunk);
	}

	.theme-switch:focus-visible {
		outline: 2px solid var(--focus-ring);
		outline-offset: 2px;
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
