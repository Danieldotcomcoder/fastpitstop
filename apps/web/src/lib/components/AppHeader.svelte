<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { brand } from '$lib/brand';
	import DelayIndicator from './DelayIndicator.svelte';
	import DelaySlider from './DelaySlider.svelte';
	import TapToSync from './TapToSync.svelte';
	import { activeStreamSession, type ActiveStreamSession } from '$lib/live-session';

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

	let session = $state<ActiveStreamSession | null>(null);
	$effect(() => activeStreamSession.subscribe((value) => (session = value)));

	let calibratorOpen = $state(false);

	function closeCalibrator(): void {
		calibratorOpen = false;
	}
</script>

<header class="app-header">
	<div class="brand-block">
		<a class="wordmark" href={resolve('/live')}>{brand.name}</a>
		<a class="beta-chip" href={resolve('/about')} title="About this beta">{brand.betaLabel}</a>
	</div>
	<nav class="app-nav" aria-label="Primary">
		{#each links as link}
			<a href={resolve(link.href)} class:active={isActive(link.href)}>{link.label}</a>
		{/each}
	</nav>
	{#if session}
		<div class="delay-sync-chrome">
			<button
				type="button"
				class="delay-sync-trigger"
				aria-expanded={calibratorOpen}
				aria-haspopup="dialog"
				onclick={() => (calibratorOpen = !calibratorOpen)}
			>
				<DelayIndicator controller={session.controller} />
			</button>
			{#if calibratorOpen}
				<div class="calibrator" role="dialog" aria-label="Delay Sync calibrator">
					<p class="calibrator-label">{session.label}</p>
					<DelaySlider controller={session.controller} />
					<TapToSync
						controller={session.controller}
						anchorEventTimeUtc={session.anchorEventTimeUtc}
						label="Tap on a known moment"
					/>
					<p class="calibrator-hint">
						Set this to how far behind the broadcast you are. Nothing on screen ever gets ahead
						of it.
					</p>
					<button type="button" class="calibrator-close" onclick={closeCalibrator}>Close</button>
				</div>
				<button
					type="button"
					class="calibrator-scrim"
					aria-label="Close calibrator"
					onclick={closeCalibrator}
				></button>
			{/if}
		</div>
	{/if}
</header>

<style>
	.app-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-4);
		padding: var(--space-3) var(--space-4);
		border-bottom: 1px solid var(--hairline);
		background: var(--surface-base);
	}

	.brand-block {
		display: flex;
		align-items: baseline;
		gap: var(--space-2);
		min-width: 0;
	}

	.wordmark {
		font-family: var(--font-display);
		font-size: var(--font-size-md);
		font-weight: 600;
		letter-spacing: var(--tracking-display);
		color: var(--text-primary);
		text-decoration: none;
		white-space: nowrap;
	}

	.wordmark:hover {
		opacity: 1;
	}

	.beta-chip {
		font-family: var(--font-ui);
		font-size: var(--font-size-xs);
		font-weight: 500;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		text-decoration: none;
		color: var(--text-muted);
		border: 1px solid var(--hairline-strong);
		padding: 0.1rem 0.4rem;
		line-height: 1.2;
	}

	.beta-chip:hover {
		color: var(--text-secondary);
		border-color: var(--text-secondary);
		opacity: 1;
	}

	.app-nav {
		display: flex;
		gap: var(--space-4);
		font-size: var(--font-size-sm);
		font-weight: 500;
	}

	.app-nav a {
		color: var(--text-muted);
		text-decoration: none;
	}

	.app-nav a:hover {
		color: var(--text-secondary);
		opacity: 1;
	}

	.app-nav a.active {
		color: var(--text-primary);
		border-bottom: 1px solid var(--hairline-strong);
		padding-bottom: 1px;
	}

	.delay-sync-chrome {
		position: relative;
	}

	.delay-sync-trigger {
		background: none;
		border: none;
		padding: 0;
		cursor: pointer;
		font: inherit;
	}

	.calibrator {
		position: absolute;
		top: calc(100% + var(--space-2));
		right: 0;
		z-index: 30;
		width: 18rem;
		max-width: calc(100vw - 2rem);
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		padding: var(--space-4);
		background: var(--surface-overlay);
		border: 1px solid var(--hairline-strong);
		border-radius: var(--radius-md);
		box-shadow: 0 8px 24px rgb(0 0 0 / 0.4);
	}

	.calibrator-label {
		margin: 0;
		font-size: var(--font-size-sm);
		font-weight: 600;
		color: var(--text-primary);
	}

	.calibrator-hint {
		margin: 0;
		font-size: var(--font-size-xs);
		color: var(--text-muted);
	}

	.calibrator-close {
		align-self: flex-end;
		background: none;
		border: none;
		color: var(--text-secondary);
		font-size: var(--font-size-xs);
		text-decoration: underline;
		cursor: pointer;
		padding: 0;
	}

	.calibrator-scrim {
		position: fixed;
		inset: 0;
		z-index: 20;
		background: transparent;
		border: none;
		cursor: default;
	}

	@media (max-width: 640px) {
		.app-header {
			flex-wrap: wrap;
			row-gap: var(--space-2);
		}

		.app-nav {
			width: 100%;
			justify-content: flex-start;
			gap: var(--space-3);
		}

		/* Thumb-reachable: the trust contract pins to the bottom on mobile,
		   not buried at the top where a phone-in-hand user can't reach it. */
		.delay-sync-chrome {
			position: fixed;
			left: var(--space-3);
			right: var(--space-3);
			bottom: var(--space-3);
			z-index: 40;
		}

		.delay-sync-trigger {
			display: block;
			width: 100%;
			background: var(--surface-raised);
			border: 1px solid var(--hairline-strong);
			border-radius: 999px;
			padding: var(--space-2) var(--space-3);
			box-shadow: 0 4px 16px rgb(0 0 0 / 0.5);
		}

		.delay-sync-trigger :global(.indicator) {
			width: 100%;
			justify-content: center;
			border: none;
			padding: 0;
		}

		.calibrator {
			position: fixed;
			left: var(--space-3);
			right: var(--space-3);
			bottom: calc(var(--space-3) + 3.25rem);
			top: auto;
			width: auto;
			max-width: none;
		}
	}
</style>
