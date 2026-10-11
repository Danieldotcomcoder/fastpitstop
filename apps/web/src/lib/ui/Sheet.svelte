<!--
  A bottom sheet for phones, built on <dialog> so focus is trapped and
  Escape closes it natively. A tap on the dimmed backdrop closes it too.
-->
<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		open: boolean;
		title: string;
		onClose: () => void;
		children: Snippet;
	}

	let { open, title, onClose, children }: Props = $props();

	const uid = $props.id();
	let dialog: HTMLDialogElement | undefined = $state();

	$effect(() => {
		if (!dialog) return;
		if (open && !dialog.open) dialog.showModal();
		if (!open && dialog.open) dialog.close();
	});

	function onBackdrop(event: MouseEvent): void {
		if (event.target === dialog) onClose();
	}
</script>

<dialog
	class="sheet"
	bind:this={dialog}
	aria-labelledby="{uid}-title"
	onclose={onClose}
	onclick={onBackdrop}
>
	<div class="body">
		<header>
			<h2 id="{uid}-title">{title}</h2>
			<button type="button" class="close" onclick={onClose} aria-label="Close">✕</button>
		</header>
		{@render children()}
	</div>
</dialog>

<style>
	.sheet {
		margin: auto 0 0;
		width: 100%;
		max-width: 100%;
		max-height: 85dvh;
		padding: 0;
		border: 0;
		border-radius: var(--radius-lg) var(--radius-lg) 0 0;
		background: var(--panel);
		color: var(--ink);
		box-shadow: var(--elev-3);
	}
	.sheet::backdrop {
		background: rgba(16, 18, 20, 0.5);
	}
	.body {
		padding: var(--space-3) var(--space-4) calc(var(--space-5) + env(safe-area-inset-bottom));
	}
	header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-3);
		margin-bottom: var(--space-3);
	}
	h2 {
		margin: 0;
		font: 500 var(--font-size-lg) / 1.2 var(--font-display);
	}
	.close {
		min-width: 44px;
		min-height: 44px;
		border: 0;
		background: none;
		color: var(--ink-2);
		font-size: 18px;
		cursor: pointer;
	}
	@media (min-width: 640px) {
		.sheet {
			margin: auto;
			max-width: 480px;
			border-radius: var(--radius-lg);
		}
	}
	@media (prefers-reduced-motion: no-preference) {
		.sheet[open] {
			animation: rise var(--duration-move) var(--ease-out);
		}
	}
	@keyframes rise {
		from {
			transform: translateY(24px);
			opacity: 0;
		}
	}
</style>
