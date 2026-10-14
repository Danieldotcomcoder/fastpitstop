/** Keep the screen awake while a race is on (ROADMAP W7): a phone beside
 * the TV should not dim mid-stint. The Screen Wake Lock API releases the
 * lock whenever the page is hidden, so it is taken again each time the page
 * comes back, for as long as the viewer wants it on. The viewer's choice is
 * remembered on this device; nothing else is stored. */

/** The slice of `navigator.wakeLock` this module uses. */
export interface WakeLockApi {
	request(type: 'screen'): Promise<WakeSentinel>;
}

export interface WakeSentinel {
	release(): Promise<void>;
	addEventListener(type: 'release', listener: () => void): void;
}

/** The slice of `document` it watches. */
export interface PageVisibility {
	readonly visibilityState: string;
	addEventListener(type: 'visibilitychange', listener: () => void): void;
	removeEventListener(type: 'visibilitychange', listener: () => void): void;
}

export const WAKE_LOCK_KEY = 'fps-wake-lock';

/** `on` is what the viewer asked for; `held` is whether the browser has
 * granted it right now (it may refuse, say on low battery). */
export interface WakeState {
	on: boolean;
	held: boolean;
}

export class ScreenWake {
	private sentinel: WakeSentinel | null = null;
	private state: WakeState = { on: false, held: false };
	private readonly listeners = new Set<(state: WakeState) => void>();
	private readonly onVisibility = () => {
		if (this.page.visibilityState === 'visible' && this.state.on) void this.acquire();
	};

	constructor(
		private readonly api: WakeLockApi,
		private readonly page: PageVisibility
	) {
		page.addEventListener('visibilitychange', this.onVisibility);
	}

	get current(): WakeState {
		return this.state;
	}

	subscribe(listener: (state: WakeState) => void): () => void {
		this.listeners.add(listener);
		listener(this.state);
		return () => this.listeners.delete(listener);
	}

	async set(on: boolean): Promise<void> {
		this.publish({ on, held: on && this.sentinel !== null });
		if (on) await this.acquire();
		else await this.drop();
	}

	async dispose(): Promise<void> {
		this.page.removeEventListener('visibilitychange', this.onVisibility);
		await this.drop();
		this.listeners.clear();
	}

	private async acquire(): Promise<void> {
		if (this.sentinel !== null || this.page.visibilityState !== 'visible') return;
		try {
			const sentinel = await this.api.request('screen');
			// Switched off while the request was in flight.
			if (!this.state.on) {
				await sentinel.release();
				return;
			}
			this.sentinel = sentinel;
			// The browser lets go whenever the page is hidden; it is taken
			// again when the page is visible.
			sentinel.addEventListener('release', () => {
				if (this.sentinel !== sentinel) return;
				this.sentinel = null;
				this.publish({ on: this.state.on, held: false });
			});
			this.publish({ on: true, held: true });
		} catch {
			this.publish({ on: this.state.on, held: false });
		}
	}

	private async drop(): Promise<void> {
		const sentinel = this.sentinel;
		this.sentinel = null;
		this.publish({ on: this.state.on, held: false });
		if (sentinel) await sentinel.release().catch(() => undefined);
	}

	private publish(state: WakeState): void {
		this.state = state;
		for (const listener of this.listeners) listener(state);
	}
}

export function rememberedWake(): boolean {
	try {
		return localStorage.getItem(WAKE_LOCK_KEY) === 'on';
	} catch {
		return false;
	}
}

export function rememberWake(on: boolean): void {
	try {
		if (on) localStorage.setItem(WAKE_LOCK_KEY, 'on');
		else localStorage.removeItem(WAKE_LOCK_KEY);
	} catch {
		// Private mode or blocked storage: the toggle still works this visit.
	}
}
