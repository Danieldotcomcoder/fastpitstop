/** A thin wrapper over `/stream/{key}` — connects, parses the timestamped
 * `{event_time_utc, kind, payload}` envelopes `f1_api.schemas.StreamEnvelope`
 * defines, and republishes them as a typed event source. Deliberately dumb:
 * no buffering, no replay, no ordering guarantees beyond what the socket
 * gives us — Delay Sync (holding events back to match the user's playback
 * clock, per CLAUDE.md's spoiler-safety invariant) is Phase 3.3's job, built
 * on top of this, not inside it.
 *
 * `StreamEnvelope` is hand-typed rather than generated: FastAPI does not
 * emit WebSocket routes into `openapi.json` (confirmed against the committed
 * file — only the REST paths appear), so there is no generated type to reuse
 * here. This mirrors `f1_api.schemas.StreamEnvelope` field-for-field; keep
 * the two in sync by hand if that shape ever changes. */
import { wsBaseUrl } from './config';

export interface StreamEnvelope {
	event_time_utc: string;
	kind: string;
	payload: Record<string, unknown>;
}

export type StreamMode = 'replay' | 'live';

export interface SessionStreamOptions {
	mode?: StreamMode;
	/** Only meaningful for `mode: 'replay'`. `0` = as fast as possible, `1` = real time. */
	speed?: number;
	/** Only meaningful for `mode: 'live'`: how far behind live the viewer
	 * runs, in seconds (their Delay Sync delay). The server sends gap
	 * history in full this far back (ROADMAP W6: catch-up, long pauses). */
	historySeconds?: number;
}

type MessageListener = (envelope: StreamEnvelope) => void;
type ErrorListener = (event: Event) => void;
type CloseListener = (event: CloseEvent) => void;

/** One connection to one session's `/stream/{key}`. Call `connect()` to open
 * the socket, subscribe with `onMessage`/`onError`/`onClose` (each returns an
 * unsubscribe function), and `close()` when done — a component's own
 * teardown (`$effect`'s cleanup, `onDestroy`) is expected to call it. */
export class SessionStream {
	private socket: WebSocket | null = null;
	private readonly messageListeners = new Set<MessageListener>();
	private readonly errorListeners = new Set<ErrorListener>();
	private readonly closeListeners = new Set<CloseListener>();

	constructor(
		private readonly sessionKey: string,
		private readonly options: SessionStreamOptions = {}
	) {}

	get readyState(): number {
		return this.socket?.readyState ?? WebSocket.CLOSED;
	}

	connect(): void {
		if (this.socket) return;
		const { mode = 'replay', speed = 1, historySeconds } = this.options;
		const url = new URL(`${wsBaseUrl()}/stream/${encodeURIComponent(this.sessionKey)}`);
		url.searchParams.set('mode', mode);
		url.searchParams.set('speed', String(speed));
		if (mode === 'live' && historySeconds !== undefined && historySeconds > 0) {
			url.searchParams.set('history', String(Math.ceil(historySeconds)));
		}

		const socket = new WebSocket(url);
		socket.addEventListener('message', (event: MessageEvent<string>) => {
			const envelope = JSON.parse(event.data) as StreamEnvelope;
			for (const listener of this.messageListeners) listener(envelope);
		});
		socket.addEventListener('error', (event) => {
			for (const listener of this.errorListeners) listener(event);
		});
		socket.addEventListener('close', (event) => {
			for (const listener of this.closeListeners) listener(event);
		});
		this.socket = socket;
	}

	onMessage(listener: MessageListener): () => void {
		this.messageListeners.add(listener);
		return () => this.messageListeners.delete(listener);
	}

	onError(listener: ErrorListener): () => void {
		this.errorListeners.add(listener);
		return () => this.errorListeners.delete(listener);
	}

	onClose(listener: CloseListener): () => void {
		this.closeListeners.add(listener);
		return () => this.closeListeners.delete(listener);
	}

	close(): void {
		this.socket?.close();
		this.socket = null;
	}
}
