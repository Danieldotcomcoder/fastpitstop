<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { brand } from '$lib/brand';
	import { apiBaseUrl } from '$lib/api/config';

	let whatDoing = $state('');
	let whatHappened = $state('');
	let whatExpected = $state('');
	let replyEmail = $state('');
	let companyWebsite = $state('');
	let status = $state<'idle' | 'sending' | 'ok' | 'error'>('idle');
	let message = $state('');
	let formStartedAtMs = $state(0);

	const routeContext = $derived(page.url.searchParams.get('route') ?? page.url.pathname);

	const diagnostics = $derived({
		route: routeContext,
		version: brand.version,
		viewport: typeof window !== 'undefined' ? `${window.innerWidth}x${window.innerHeight}` : '',
		userAgent: typeof navigator !== 'undefined' ? navigator.userAgent : '',
		delayOffset: typeof localStorage !== 'undefined' ? localStorage.getItem('fps:delay') : null
	});

	onMount(() => {
		formStartedAtMs = Date.now();
	});

	async function onSubmit(event: Event): Promise<void> {
		event.preventDefault();
		status = 'sending';
		message = '';
		try {
			const response = await fetch(`${apiBaseUrl()}/feedback`, {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({
					what_doing: whatDoing,
					what_happened: whatHappened,
					what_expected: whatExpected,
					reply_email: replyEmail || null,
					route: routeContext,
					diagnostics,
					form_started_at_ms: formStartedAtMs,
					turnstile_token: 'dev',
					company_website: companyWebsite
				})
			});
			const body = (await response.json().catch(() => ({}))) as {
				ok?: boolean;
				message?: string;
				detail?: string;
			};
			if (!response.ok) {
				status = 'error';
				message = body.detail ?? 'Could not send feedback. Try again in a moment.';
				return;
			}
			status = 'ok';
			message = body.message ?? 'Thanks — we got it.';
			whatDoing = '';
			whatHappened = '';
			whatExpected = '';
			replyEmail = '';
		} catch {
			status = 'error';
			message = 'Could not reach the API — is it running?';
		}
	}
</script>

<svelte:head>
	<title>Feedback · {brand.name}</title>
</svelte:head>

<article>
	<h1>Send feedback</h1>
	<p>
		What were you doing, what happened, and what did you expect? Optional email only if you want a
		reply.
	</p>

	<form class="form" onsubmit={onSubmit}>
		<label>
			<span>What were you doing?</span>
			<textarea bind:value={whatDoing} maxlength="2000" required rows="3"></textarea>
		</label>
		<label>
			<span>What happened?</span>
			<textarea bind:value={whatHappened} maxlength="2000" required rows="3"></textarea>
		</label>
		<label>
			<span>What did you expect?</span>
			<textarea bind:value={whatExpected} maxlength="2000" required rows="3"></textarea>
		</label>
		<label>
			<span>Email for a reply (optional)</span>
			<input type="email" bind:value={replyEmail} maxlength="254" autocomplete="email" />
		</label>

		<!-- Honeypot: hidden from humans, bots fill it. -->
		<label class="hp" aria-hidden="true">
			<span>Company website</span>
			<input type="text" bind:value={companyWebsite} tabindex="-1" autocomplete="off" />
		</label>

		<details class="diagnostics">
			<summary>Diagnostic context attached with this report</summary>
			<pre class="tabular">{JSON.stringify(diagnostics, null, 2)}</pre>
		</details>

		<p class="turnstile-note">
			Cloudflare Turnstile will sit here once site keys are configured. In local/dev the API accepts
			submissions without a live challenge.
		</p>

		<button type="submit" disabled={status === 'sending'}>
			{status === 'sending' ? 'Sending…' : 'Send feedback'}
		</button>

		{#if message}
			<p class="result" class:ok={status === 'ok'} class:err={status === 'error'} role="status">
				{message}
			</p>
		{/if}
	</form>
</article>

<style>
	.form {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
		max-width: 36rem;
	}

	label {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		font-size: var(--font-size-sm);
		color: var(--text-secondary);
	}

	textarea,
	input[type='email'],
	input[type='text'] {
		font: inherit;
		color: var(--text-primary);
		background: var(--surface-raised);
		border: 1px solid var(--hairline-strong);
		padding: var(--space-2) var(--space-3);
		resize: vertical;
	}

	.hp {
		position: absolute;
		left: -10000px;
		top: auto;
		width: 1px;
		height: 1px;
		overflow: hidden;
	}

	.diagnostics {
		font-size: var(--font-size-sm);
		color: var(--text-muted);
	}

	.diagnostics pre {
		margin: var(--space-2) 0 0;
		padding: var(--space-3);
		border: 1px solid var(--hairline);
		background: var(--surface-raised);
		overflow: auto;
		font-family: var(--font-mono);
		font-size: var(--font-size-xs);
		font-variant-numeric: tabular-nums;
	}

	.turnstile-note {
		margin: 0;
		font-size: var(--font-size-xs);
		color: var(--text-muted);
	}

	button[type='submit'] {
		align-self: flex-start;
		background: var(--surface-overlay);
		border: 1px solid var(--hairline-strong);
		color: var(--text-primary);
		padding: var(--space-2) var(--space-4);
		font-weight: 500;
		cursor: pointer;
	}

	button[type='submit']:disabled {
		opacity: 0.6;
		cursor: wait;
	}

	.result {
		margin: 0;
		font-size: var(--font-size-sm);
	}

	.result.ok {
		color: var(--text-primary);
	}

	.result.err {
		color: var(--flag-red);
	}
</style>
