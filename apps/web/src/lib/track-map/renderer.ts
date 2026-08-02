/** The Canvas2D drawing layer. Pure drawing over an already-resolved
 * `DrawFrame` (`frame.ts`) — all interpolation, dominance-to-arc conversion
 * and team-color lookup happen before this class ever sees a frame, so this
 * stays "draw what you're told" and every judgment call lives in code that
 * is unit-testable without a `CanvasRenderingContext2D`.
 *
 * Single canvas, full-clear-and-redraw every frame: at this shape count (one
 * ~200-point outline, up to `DEFAULT_SEGMENT_COUNT` dominance arcs, ~20 car
 * markers) a full redraw is well inside a 16ms frame budget on a mid-range
 * phone, so dirty-region tracking would add real complexity for no
 * measurable gain — the "as measured" case CLAUDE.md's prompt allows for. */
import type { DrawFrame } from './frame';
import { fitOutlineToCanvas, project, type Viewport } from './geometry';
import type { Point } from './types';

const OUTLINE_COLOR = '#948c7d';
const OUTLINE_WIDTH_PX = 3;
const DOMINANCE_WIDTH_PX = 5;
const CAR_RADIUS_PX = 6;
const CAR_STROKE_COLOR = 'rgba(0, 0, 0, 0.55)';
const LABEL_FONT = '700 10px ui-monospace, Menlo, Consolas, monospace';
const LABEL_COLOR = '#1c1a17';
const LABEL_HALO_COLOR = 'rgba(255, 255, 255, 0.85)';
const STALE_ALPHA = 0.35;

export class TrackMapRenderer {
	private width = 0;
	private height = 0;
	private dpr = 1;

	constructor(private readonly ctx: CanvasRenderingContext2D) {}

	/** Resize the backing canvas for a new CSS size / device pixel ratio.
	 * Called on mount and on every `ResizeObserver` callback — never
	 * assumed constant, since the map's own panel can resize (orientation
	 * change, split view). */
	resize(widthCss: number, heightCss: number, devicePixelRatio = 1): void {
		this.width = widthCss;
		this.height = heightCss;
		this.dpr = devicePixelRatio;
		const canvas = this.ctx.canvas;
		const pixelWidth = Math.max(1, Math.round(widthCss * devicePixelRatio));
		const pixelHeight = Math.max(1, Math.round(heightCss * devicePixelRatio));
		if (canvas.width !== pixelWidth) canvas.width = pixelWidth;
		if (canvas.height !== pixelHeight) canvas.height = pixelHeight;
	}

	draw(frame: DrawFrame): void {
		const { ctx, width, height, dpr } = this;
		ctx.save();
		ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
		ctx.clearRect(0, 0, width, height);

		if (frame.outline && frame.outline.length > 1) {
			const viewport = fitOutlineToCanvas(frame.outline, width, height);
			this._drawOutline(viewport, frame.outline);
			for (const segment of frame.segments) {
				this._drawDominanceSegment(viewport, segment);
			}
			for (const car of frame.cars) {
				this._drawCar(viewport, car);
			}
		}

		ctx.restore();
	}

	private _drawOutline(viewport: Viewport, outline: readonly Point[]): void {
		const { ctx } = this;
		ctx.beginPath();
		this._tracePath(ctx, viewport, outline);
		ctx.strokeStyle = OUTLINE_COLOR;
		ctx.lineWidth = OUTLINE_WIDTH_PX;
		ctx.lineJoin = 'round';
		ctx.stroke();
	}

	private _drawDominanceSegment(
		viewport: Viewport,
		segment: { start: Point; end: Point; color: string }
	): void {
		// Dominance segments are drawn as short chords rather than following
		// every outline vertex between start/end — accurate enough at this
		// scale and far cheaper than re-tracing a sub-arc of the polyline.
		const { ctx } = this;
		const [sx, sy] = project(viewport, segment.start);
		const [ex, ey] = project(viewport, segment.end);
		ctx.beginPath();
		ctx.moveTo(sx, sy);
		ctx.lineTo(ex, ey);
		ctx.strokeStyle = segment.color;
		ctx.lineWidth = DOMINANCE_WIDTH_PX;
		ctx.lineCap = 'round';
		ctx.stroke();
	}

	private _drawCar(
		viewport: Viewport,
		car: { code: string | null; color: string; x: number; y: number; stale: boolean }
	): void {
		const { ctx } = this;
		const [x, y] = project(viewport, [car.x, car.y]);
		ctx.save();
		ctx.globalAlpha = car.stale ? STALE_ALPHA : 1;

		ctx.beginPath();
		ctx.arc(x, y, CAR_RADIUS_PX, 0, Math.PI * 2);
		ctx.fillStyle = car.color;
		ctx.fill();
		ctx.lineWidth = 1.5;
		ctx.strokeStyle = CAR_STROKE_COLOR;
		ctx.stroke();

		if (car.code) {
			const labelY = y - CAR_RADIUS_PX - 4;
			ctx.font = LABEL_FONT;
			ctx.textAlign = 'center';
			ctx.textBaseline = 'bottom';
			ctx.lineWidth = 3;
			ctx.strokeStyle = LABEL_HALO_COLOR;
			ctx.strokeText(car.code, x, labelY);
			ctx.fillStyle = LABEL_COLOR;
			ctx.fillText(car.code, x, labelY);
		}

		ctx.restore();
	}

	private _tracePath(
		ctx: CanvasRenderingContext2D,
		viewport: Viewport,
		points: readonly Point[]
	): void {
		points.forEach((point, index) => {
			const [x, y] = project(viewport, point);
			if (index === 0) ctx.moveTo(x, y);
			else ctx.lineTo(x, y);
		});
	}
}
