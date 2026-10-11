/** The toast queue: new race moments announced briefly at the edge of the
 * screen (ROADMAP W2 D4). Pure and clock-injected, so expiry is
 * testable; `Toaster.svelte` renders it. Feed it released moments only. */

export interface Toast {
	id: number;
	text: string;
	/** A short label above the text, e.g. "Lap 28 · Safety car". */
	kicker: string | null;
	expiresAtMs: number;
}

export class ToastQueue {
	private items: Toast[] = [];
	private nextId = 1;

	constructor(
		private readonly ttlMs = 6_000,
		private readonly maxVisible = 3
	) {}

	push(text: string, nowMs: number, kicker: string | null = null): Toast {
		const toast = { id: this.nextId++, text, kicker, expiresAtMs: nowMs + this.ttlMs };
		this.items = [...this.items, toast].slice(-this.maxVisible);
		return toast;
	}

	dismiss(id: number): void {
		this.items = this.items.filter((t) => t.id !== id);
	}

	/** Drops expired toasts; returns what is still showing. */
	visible(nowMs: number): readonly Toast[] {
		this.items = this.items.filter((t) => t.expiresAtMs > nowMs);
		return this.items;
	}
}
