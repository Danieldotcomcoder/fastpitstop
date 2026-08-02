import { describe, expect, it } from 'vitest';
import { Store } from './store';

describe('Store', () => {
	it('calls a new subscriber immediately with the current value (Svelte store contract)', () => {
		const store = new Store(1);
		const seen: number[] = [];
		store.subscribe((value) => seen.push(value));
		expect(seen).toEqual([1]);
	});

	it('notifies subscribers on set', () => {
		const store = new Store('a');
		const seen: string[] = [];
		store.subscribe((value) => seen.push(value));
		store.set('b');
		expect(seen).toEqual(['a', 'b']);
	});

	it('update() derives the next value from the current one', () => {
		const store = new Store(10);
		store.update((n) => n + 5);
		expect(store.get()).toBe(15);
	});

	it('stops notifying after unsubscribe', () => {
		const store = new Store(0);
		const seen: number[] = [];
		const unsubscribe = store.subscribe((value) => seen.push(value));
		store.set(1);
		unsubscribe();
		store.set(2);
		expect(seen).toEqual([0, 1]);
	});
});
