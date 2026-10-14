import { describe, expect, it } from 'vitest';
import { ScreenWake, type PageVisibility, type WakeSentinel } from './wake-lock';

class FakeSentinel implements WakeSentinel {
	released = false;
	private listeners: (() => void)[] = [];
	async release(): Promise<void> {
		this.released = true;
	}
	addEventListener(_type: 'release', listener: () => void): void {
		this.listeners.push(listener);
	}
	/** What the browser does when the page is hidden. */
	browserRelease(): void {
		this.released = true;
		for (const listener of this.listeners) listener();
	}
}

class FakePage implements PageVisibility {
	visibilityState = 'visible';
	private listener: (() => void) | null = null;
	addEventListener(_type: 'visibilitychange', listener: () => void): void {
		this.listener = listener;
	}
	removeEventListener(): void {
		this.listener = null;
	}
	show(state: 'visible' | 'hidden'): void {
		this.visibilityState = state;
		this.listener?.();
	}
}

function setup(refuse = false) {
	const sentinels: FakeSentinel[] = [];
	const api = {
		async request() {
			if (refuse) throw new Error('NotAllowedError');
			const sentinel = new FakeSentinel();
			sentinels.push(sentinel);
			return sentinel;
		}
	};
	const page = new FakePage();
	return { wake: new ScreenWake(api, page), page, sentinels };
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('ScreenWake', () => {
	it('holds the lock while on and lets it go when switched off', async () => {
		const { wake, sentinels } = setup();
		await wake.set(true);
		expect(wake.current).toEqual({ on: true, held: true });
		await wake.set(false);
		expect(wake.current).toEqual({ on: false, held: false });
		expect(sentinels[0]!.released).toBe(true);
	});

	it('takes the lock again when the page comes back', async () => {
		const { wake, page, sentinels } = setup();
		await wake.set(true);
		page.show('hidden');
		sentinels[0]!.browserRelease();
		expect(wake.current).toEqual({ on: true, held: false });
		page.show('visible');
		await settle();
		expect(sentinels).toHaveLength(2);
		expect(wake.current).toEqual({ on: true, held: true });
	});

	it('does not take it back once switched off', async () => {
		const { wake, page, sentinels } = setup();
		await wake.set(true);
		await wake.set(false);
		page.show('hidden');
		page.show('visible');
		await settle();
		expect(sentinels).toHaveLength(1);
	});

	it('stays on but not held when the browser refuses', async () => {
		const { wake } = setup(true);
		await wake.set(true);
		expect(wake.current).toEqual({ on: true, held: false });
	});
});
