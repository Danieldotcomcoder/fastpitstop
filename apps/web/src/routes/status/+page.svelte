<script lang="ts">
	import { onMount } from 'svelte';
	import { brand } from '$lib/brand';
	import { api, type components } from '$lib/api/client';
	import { webBuildInfo } from '$lib/api/config';
	import Panel from '$lib/components/Panel.svelte';
	import Stat from '$lib/components/Stat.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';

	type VersionOut = components['schemas']['VersionOut'];
	type ReadyOut = components['schemas']['ReadyOut'];

	const web = webBuildInfo();

	let loaded = $state(false);
	let healthy = $state(false);
	let ready = $state(false);
	let readyDetail = $state<string | null>(null);
	let lastIngestAt = $state<string | null>(null);
	let apiVersion = $state<VersionOut | null>(null);

	async function check(): Promise<void> {
		loaded = false;

		const healthResult = await api.GET('/healthz').catch(() => null);
		healthy = healthResult?.response.ok ?? false;

		const readyResult = await api.GET('/readyz').catch(() => null);
		ready = readyResult?.response.ok ?? false;
		if (ready) {
			readyDetail = null;
			lastIngestAt = (readyResult?.data as ReadyOut | undefined)?.last_ingest_at ?? null;
		} else {
			const body = readyResult?.error as { detail?: string } | undefined;
			readyDetail = body?.detail ?? 'unreachable';
			lastIngestAt = null;
		}

		const versionResult = await api.GET('/version').catch(() => null);
		apiVersion = (versionResult?.data as VersionOut | undefined) ?? null;

		loaded = true;
	}

	onMount(() => {
		void check();
	});
</script>

<svelte:head>
	<title>Status · {brand.name}</title>
</svelte:head>

<div class="status-page">
	<h1>Status</h1>
	<p class="muted">
		Live health of the {brand.name} stack — what's actually running, not what's supposed to be.
	</p>

	{#if !loaded}
		<LoadingState label="Checking…" />
	{:else}
		<Panel title="API">
			<div class="stat-row">
				<Stat label="Liveness" value={healthy ? 'up' : 'down'} tone={healthy ? 'positive' : 'negative'} />
				<Stat label="Readiness" value={ready ? 'ready' : 'not ready'} tone={ready ? 'positive' : 'negative'} />
			</div>
			{#if !ready && readyDetail}
				<p class="detail">{readyDetail}</p>
			{/if}
			{#if ready}
				<Stat label="Last ingest" value={lastIngestAt ?? 'no session ingested yet'} />
			{/if}
		</Panel>

		<Panel title="Build">
			<div class="stat-row">
				<Stat label="API commit" value={apiVersion?.git_sha ?? '—'} />
				<Stat label="API built" value={apiVersion?.build_time ?? '—'} />
			</div>
			<div class="stat-row">
				<Stat label="Web commit" value={web.gitSha} />
				<Stat label="Web built" value={web.buildTime} />
			</div>
		</Panel>

		<button class="refresh" onclick={() => void check()}>Refresh</button>
	{/if}
</div>

<style>
	.status-page {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
	}

	.muted {
		color: var(--text-muted);
		font-size: var(--font-size-sm);
	}

	.stat-row {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-5);
	}

	.detail {
		margin: 0;
		color: var(--color-negative);
		font-size: var(--font-size-sm);
	}

	.refresh {
		align-self: flex-start;
		background: var(--surface-raised);
		border: 1px solid var(--hairline);
		border-radius: var(--radius-md);
		padding: var(--space-2) var(--space-4);
		font: inherit;
		cursor: pointer;
	}

	.refresh:hover {
		background: var(--surface-hover, var(--surface-raised));
	}
</style>
