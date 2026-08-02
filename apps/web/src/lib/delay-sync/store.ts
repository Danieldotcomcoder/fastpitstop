/** A minimal observable value, deliberately hand-rolled instead of
 * importing `svelte/store`: this keeps the Delay Sync core importable and
 * testable with zero Svelte/SvelteKit runtime, per this module's
 * "framework-agnostic" brief. `subscribe(run)` calling `run` immediately
 * with the current value, then returning an unsubscribe function, is
 * exactly Svelte's own store contract — so `$store` auto-subscription works
 * unmodified on an instance of this class in any `.svelte` file, with no
 * adapter layer needed. */
export type Subscriber<T> = (value: T) => void;
export type Unsubscriber = () => void;

export interface ReadableStore<T> {
	subscribe(run: Subscriber<T>): Unsubscriber;
}

export class Store<T> implements ReadableStore<T> {
	private value: T;
	private readonly subscribers = new Set<Subscriber<T>>();

	constructor(initial: T) {
		this.value = initial;
	}

	get(): T {
		return this.value;
	}

	set(value: T): void {
		this.value = value;
		for (const subscriber of this.subscribers) subscriber(value);
	}

	update(fn: (value: T) => T): void {
		this.set(fn(this.value));
	}

	subscribe(run: Subscriber<T>): Unsubscriber {
		this.subscribers.add(run);
		run(this.value);
		return () => {
			this.subscribers.delete(run);
		};
	}
}
