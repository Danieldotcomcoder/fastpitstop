<!--
  Track: the circuit's real outline (derived from position telemetry, never
  drawn by hand) with every car on it, the design's map in Canvas.

  Positions are MEASURED whenever the session has them: F1's own position
  feed, about four samples a second per car, released through Delay Sync
  as `car_positions` frames and drawn a moment behind the clock so each car
  glides between real samples (`MeasuredPositions`). Every replayed session
  has them once its positions are backfilled. A live session does not: the
  live feed withholds positions from our unauthenticated client, and F1
  only publishes them after the session. Then each car is ESTIMATED from
  lap timing and its gap on the timing screen (placement.ts). The summary
  line says which one the viewer is looking at.
-->
<script lang="ts">
	import {
		playbackClockMs,
		serverClockOffsetMs,
		serverNowMs,
		type DelaySyncController
	} from '$lib/delay-sync';
	import { colorForTeam } from '$lib/teams';
	import type { DriverRow } from '$lib/timing-tower';
	import {
		buildArcLengthTable,
		LapProgressTracker,
		MAX_INTERPOLATION_GAP_MS,
		MeasuredPositions,
		SMOOTHING_MS,
		outlineFromApi,
		pointAtArcLength,
		type ApiTrackOutline,
		type Point
	} from '$lib/track-map';
	import type { FocusPair } from './focus';
	import { placeCars } from './placement';

	/** The circuit as `GET /sessions/{key}/circuit` sends it. The outline's
	 * points arrive loosely typed off the wire and go through
	 * `outlineFromApi`, the one place they are validated. */
	export interface DeskCircuit {
		name: string;
		lap_distance_m: number;
		corners: { number: number; letter: string | null; distance_m: number }[];
		drs_zones: { start_m: number; end_m: number }[];
		outline: ApiTrackOutline | null;
		/** Pit entry to pit exit, from the reference race's real stops. */
		pit_lane?: { points: readonly (readonly number[])[] } | null;
	}

	interface Props {
		controller: DelaySyncController;
		circuit: DeskCircuit | null;
		circuitStatus: 'loading' | 'ready' | 'missing';
		rows: DriverRow[];
		pair: FocusPair | null;
		codes: Record<string, string>;
		/** Race seconds per stream second: 1 live, the replay speed otherwise. */
		timeScale: number;
	}

	let { controller, circuit, circuitStatus, rows, pair, codes, timeScale }: Props = $props();

	const N = 720;
	/** How often `RaceWorkspace` asks Delay Sync to release: the map draws
	 * far enough behind the clock to cover one release interval. */
	const RELEASE_TICK_MS = 100;
	/** A car the feed has stopped placing for this long (race time) while it
	 * still places the others has left the track: into the garage, or
	 * retired out of sight. When the whole feed stops, nobody is hidden. */
	const GONE_AFTER_RACE_MS = 10_000;
	const measuredPositions = new MeasuredPositions();
	const progress = new LapProgressTracker();

	let canvas: HTMLCanvasElement | undefined = $state();
	let wrap: HTMLDivElement | undefined = $state();
	let estimated = $state(true);
	let pitCodes = $state<string[]>([]);

	let outline = $derived(outlineFromApi(circuit?.outline ?? null));
	/** The outline resampled evenly by arc length: index k is k/N of a lap. */
	let samples = $derived.by((): Point[] | null => {
		if (!outline || outline.points.length < 2) return null;
		const table = buildArcLengthTable(outline.points);
		return Array.from({ length: N }, (_, k) => pointAtArcLength(table, k / N));
	});
	let lapLength = $derived(circuit?.lap_distance_m ?? null);
	/** The pit lane in track units. Cars in the pits and their garages are
	 * measured there; without it they sat in empty space beside the track. */
	let pitLane = $derived.by((): Point[] | null => {
		const out: Point[] = [];
		for (const point of circuit?.pit_lane?.points ?? []) {
			const x = point[0];
			const y = point[1];
			if (typeof x === 'number' && typeof y === 'number') out.push([x, y]);
		}
		return out.length >= 2 ? out : null;
	});

	$effect(() => {
		void outline;
		progress.reset();
	});
	$effect(() => {
		void controller;
		measuredPositions.reset();
		progress.reset();
	});

	// --- geometry -------------------------------------------------------------
	let view = { w: 0, h: 0, dpr: 1, scale: 1, ox: 0, oy: 0, minX: 0, maxY: 0 };

	function fit(): void {
		if (!wrap || !canvas || !samples) return;
		const rect = wrap.getBoundingClientRect();
		const dpr = Math.min(2, window.devicePixelRatio || 1);
		canvas.width = Math.round(rect.width * dpr);
		canvas.height = Math.round(rect.height * dpr);
		const shape = pitLane ? [...samples, ...pitLane] : samples;
		const xs = shape.map((p) => p[0]);
		const ys = shape.map((p) => p[1]);
		const minX = Math.min(...xs);
		const maxX = Math.max(...xs);
		const minY = Math.min(...ys);
		const maxY = Math.max(...ys);
		const pad = 34;
		const scale = Math.min(
			(rect.width - pad * 2) / Math.max(1, maxX - minX),
			(rect.height - pad * 2) / Math.max(1, maxY - minY)
		);
		view = {
			w: rect.width,
			h: rect.height,
			dpr,
			scale,
			ox: (rect.width - (maxX - minX) * scale) / 2,
			oy: (rect.height - (maxY - minY) * scale) / 2,
			minX,
			maxY
		};
	}

	/** Outline coordinates are y-up (as the car sensors report them); the
	 * canvas is y-down, so the shape is flipped rather than mirrored. */
	function toScreen(p: Point): Point {
		return [view.ox + (p[0] - view.minX) * view.scale, view.oy + (view.maxY - p[1]) * view.scale];
	}
	const P = (k: number): Point => toScreen(samples![((k % N) + N) % N]!);
	function normalAt(k: number): Point {
		const a = P(k - 2);
		const b = P(k + 2);
		const dx = b[0] - a[0];
		const dy = b[1] - a[1];
		const l = Math.hypot(dx, dy) || 1;
		return [dy / l, -dx / l];
	}
	function outward(k: number): Point {
		const p = P(k);
		const n = normalAt(k);
		const c: Point = [view.w / 2, view.h / 2];
		const s = (p[0] - c[0]) * n[0] + (p[1] - c[1]) * n[1] > 0 ? 1 : -1;
		return [n[0] * s, n[1] * s];
	}
	// --- drawing --------------------------------------------------------------
	let colors: Record<string, string> = {};
	function readColors(): void {
		if (!canvas) return;
		const style = getComputedStyle(canvas);
		const v = (name: string) => style.getPropertyValue(name).trim();
		colors = {
			track: v('--track'),
			edge: v('--track-edge'),
			ink: v('--ink'),
			ink2: v('--ink-2'),
			muted: v('--muted'),
			panel: v('--panel'),
			paper: v('--paper'),
			accent: v('--accent')
		};
	}

	function strokeRange(
		ctx: CanvasRenderingContext2D,
		u0: number,
		u1: number,
		width: number,
		color: string,
		cap: 'butt' | 'round' | 'square' = 'round'
	): void {
		const k0 = Math.round(u0 * N);
		let k1 = Math.round(u1 * N);
		if (k1 < k0) k1 += N; // a zone across the start/finish line
		ctx.beginPath();
		for (let k = k0; k <= k1; k++) {
			const [x, y] = P(k);
			if (k === k0) ctx.moveTo(x, y);
			else ctx.lineTo(x, y);
		}
		ctx.lineWidth = width;
		ctx.strokeStyle = color;
		ctx.lineCap = cap;
		ctx.lineJoin = 'round';
		ctx.stroke();
	}

	/** `frameWallMs` is the wall-clock instant the frame will be shown at
	 * (the animation frame's own timestamp). Reading the clock inside the
	 * draw instead made motion uneven: on the frames where Delay Sync
	 * releases (every 100 ms), the release and the panels' updates run
	 * first, the draw starts a few milliseconds late, and every car jumped
	 * ahead on that frame and lagged on the next. */
	function draw(frameWallMs: number = serverNowMs()): void {
		if (!canvas || !samples || !view.w) return;
		const ctx = canvas.getContext('2d');
		if (!ctx) return;
		const c = colors;
		ctx.setTransform(view.dpr, 0, 0, view.dpr, 0, 0);
		ctx.clearRect(0, 0, view.w, view.h);
		const tw = Math.max(7, Math.min(12, Math.min(view.w, view.h) / 42));

		// The pit lane first, narrower, so the racing line covers where they meet.
		if (pitLane) {
			const pw = Math.max(3, tw * 0.45);
			const lane = pitLane.map(toScreen);
			for (const [width, color] of [
				[pw + 3, c.edge ?? '#a4a89f'],
				[pw, c.track ?? '#cdd0c8']
			] as const) {
				ctx.beginPath();
				lane.forEach(([x, y], i) => (i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)));
				ctx.lineWidth = width;
				ctx.strokeStyle = color;
				ctx.lineCap = 'round';
				ctx.lineJoin = 'round';
				ctx.stroke();
			}
		}

		strokeRange(ctx, 0, 1.001, tw + 3, c.edge ?? '#a4a89f');
		strokeRange(ctx, 0, 1.001, tw, c.track ?? '#cdd0c8');

		// DRS zones, placed by distance along the lap.
		if (lapLength) {
			for (const zone of circuit?.drs_zones ?? []) {
				strokeRange(
					ctx,
					zone.start_m / lapLength,
					zone.end_m / lapLength,
					3,
					c.accent ?? '#2442b5',
					'butt'
				);
			}
		}

		// Start/finish.
		{
			const p = P(0);
			const n = normalAt(0);
			const o = outward(0);
			const L = tw / 2 + 5;
			ctx.beginPath();
			ctx.moveTo(p[0] - n[0] * L, p[1] - n[1] * L);
			ctx.lineTo(p[0] + n[0] * L, p[1] + n[1] * L);
			ctx.strokeStyle = c.ink ?? '#16191c';
			ctx.lineWidth = 2;
			ctx.lineCap = 'butt';
			ctx.stroke();
			ctx.font = '600 10px "IBM Plex Mono", monospace';
			ctx.fillStyle = c.ink ?? '#16191c';
			ctx.textAlign = 'center';
			ctx.textBaseline = 'middle';
			ctx.fillText('S/F', p[0] - o[0] * (tw + 12), p[1] - o[1] * (tw + 12));
		}

		// Corner numbers.
		if (lapLength) {
			ctx.font = '500 10.5px "IBM Plex Mono", monospace';
			ctx.fillStyle = c.muted ?? '#646a6f';
			for (const corner of circuit?.corners ?? []) {
				const k = Math.round((corner.distance_m / lapLength) * N);
				const p = P(k);
				const o = outward(k);
				ctx.fillText(
					`${corner.number}${corner.letter ?? ''}`,
					p[0] + o[0] * (tw + 10),
					p[1] + o[1] * (tw + 10)
				);
			}
		}

		// Cars.
		const state = controller.visibleState.get();
		progress.update(state.events);
		measuredPositions.update(state.positionFrames);
		const clockMs = playbackClockMs(frameWallMs, controller.delayState.get().delaySeconds);
		const measured = measuredPositions.hasPositions;
		estimated = !measured;
		const drawMs = measured
			? clockMs - MeasuredPositions.lagMs(RELEASE_TICK_MS, timeScale)
			: clockMs;
		const maxGapMs = MAX_INTERPOLATION_GAP_MS / Math.max(1, timeScale);
		const smoothMs = SMOOTHING_MS / Math.max(1, timeScale);
		const goneAfterMs = GONE_AFTER_RACE_MS / Math.max(1, timeScale);

		const pa = pair?.a ?? null;
		const pb = pair?.b ?? null;
		// Leader from its own lap timing, everyone else behind it by their
		// gap on the timing screen (see placement.ts for why).
		const fractions = measured
			? null
			: placeCars(rows, {
					lapsSinceLine: (d) => progress.lapsSinceLine(d, clockMs, timeScale),
					lapTimeS: (d) => progress.lapTimeS(d)
				});
		// Measured, a car is drawn wherever the feed put it, classified or
		// not (practice, the formation lap); estimated, only a classified car
		// has anywhere to be.
		const placed = rows
			.filter((r) => measured || r.position !== null)
			.sort(
				(x, y) =>
					Number(x.driver === pa || x.driver === pb) - Number(y.driver === pa || y.driver === pb) ||
					(y.position ?? 99) - (x.position ?? 99)
			);
		const inPit: string[] = [];
		for (const row of placed) {
			let k: number;
			let x: number;
			let y: number;
			const isPair = row.driver === pa || row.driver === pb;
			if (measured) {
				const at = measuredPositions.at(row.driver, drawMs, maxGapMs, smoothMs);
				if (at === null || at.behindFeedMs > goneAfterMs) continue;
				[x, y] = toScreen([at.x, at.y]);
				k = 0;
			} else {
				const fraction = fractions?.get(row.driver) ?? null;
				if (fraction === null) continue;
				k = Math.round(fraction * N);
				[x, y] = P(k);
			}
			const pitting = row.inPit || progress.inPit(row.driver);
			if (pitting) {
				inPit.push(codes[row.driver] ?? row.driver);
				// A measured car is already physically in the pit lane; an
				// estimated one is nudged off the racing line to show it.
				if (!measured) {
					const o = outward(k);
					x -= o[0] * (tw + 8);
					y -= o[1] * (tw + 8);
				}
			}
			const radius = isPair ? 7 : 5;
			const color = colorForTeam(row.team);
			ctx.beginPath();
			ctx.arc(x, y, radius + 2, 0, Math.PI * 2);
			ctx.fillStyle = c.panel ?? '#f9f9f6';
			ctx.fill();
			ctx.beginPath();
			ctx.arc(x, y, radius, 0, Math.PI * 2);
			if (pitting) {
				ctx.lineWidth = 2;
				ctx.strokeStyle = color;
				ctx.stroke();
			} else {
				ctx.fillStyle = color;
				ctx.fill();
			}
		}
		// Only when it changes: a fresh array every frame re-rendered the
		// summary sixty times a second.
		if (inPit.join() !== pitCodes.join()) pitCodes = inPit;
	}

	// --- lifecycle --------------------------------------------------------------
	$effect(() => {
		if (!canvas || !wrap || !samples) return;
		readColors();
		fit();
		const observer = new ResizeObserver(() => {
			fit();
			draw();
		});
		observer.observe(wrap);
		const scheme = window.matchMedia('(prefers-color-scheme: dark)');
		const themeObserver = new MutationObserver(readColors);
		themeObserver.observe(document.documentElement, {
			attributes: true,
			attributeFilter: ['data-theme']
		});
		scheme.addEventListener('change', readColors);
		const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
		let handle = 0;
		let last = 0;
		const loop = (now: number) => {
			// Reduced motion: one redraw a second instead of a continuous glide.
			if (!reduced.matches || now - last >= 1000) {
				last = now;
				draw(performance.timeOrigin + now + serverClockOffsetMs());
			}
			handle = requestAnimationFrame(loop);
		};
		handle = requestAnimationFrame(loop);
		return () => {
			cancelAnimationFrame(handle);
			observer.disconnect();
			themeObserver.disconnect();
			scheme.removeEventListener('change', readColors);
		};
	});

	let leader = $derived(rows.find((r) => r.position === 1) ?? null);
</script>

<section class="panel p-map" aria-labelledby="mapTitle" data-testid="map-panel">
	<div class="panel-head">
		<div>
			<h2 class="panel-title" id="mapTitle">Track</h2>
			<p class="panel-sub">
				{#if circuit}{circuit.name}{#if lapLength}, {(lapLength / 1000).toFixed(3)} km{/if}{:else}The
					circuit and every car on it{/if}
			</p>
		</div>
	</div>
	{#if samples}
		<div class="map-wrap" bind:this={wrap} role="img" aria-label="Track map with car positions">
			<canvas bind:this={canvas} aria-hidden="true"></canvas>
		</div>
		<div class="map-summary">
			{#if leader}{codes[leader.driver] ?? leader.driver} leads{#if pitCodes.length}
					· In the pit lane: {pitCodes.join(', ')}{/if}.{/if}
			{#if estimated}Positions are estimated from the leader’s lap timing and each car’s gap on the
				timing screen; the feed does not carry measured positions for this session.{:else}Measured
				positions from F1’s position data, about four a second per car.{/if}
		</div>
		<div class="map-legend">
			<span><i class="dot" style="background:var(--ink-2)"></i>Car, in team colour</span>
			<span><i class="dot hollow"></i>In the pit lane</span>
			{#if (circuit?.drs_zones ?? []).length}<span><i class="dash"></i>DRS zone</span>{/if}
		</div>
	{:else if circuitStatus === 'loading'}
		<!-- The map's own frame, held while the circuit loads: a one-line
		     note that then became a 16:10 map pushed every panel below it
		     down (measured by `just verify-real`). -->
		<div class="map-wrap map-wrap-loading" role="status">
			<p class="map-loading-note">Loading the circuit…</p>
		</div>
		<div class="map-summary">&nbsp;</div>
		<div class="map-legend" aria-hidden="true">
			<span><i class="dot" style="background:var(--ink-2)"></i>Car, in team colour</span>
			<span><i class="dot hollow"></i>In the pit lane</span>
		</div>
	{:else}
		<div class="empty">
			No outline for this circuit yet, so there is nothing to draw the cars on.
		</div>
	{/if}
</section>
