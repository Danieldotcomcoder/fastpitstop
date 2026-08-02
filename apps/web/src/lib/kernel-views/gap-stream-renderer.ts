/** Canvas drawing for the gap-to-leader stream graph — "draw what you're
 * told" over an already-built `GapStreamFrame`, the same split
 * `$lib/track-map/renderer.ts` uses so every judgment call lives in
 * unit-testable code, not here.
 *
 * Canvas, not SVG, per this prompt's own steer ("high-point-count charts
 * like the stream graph favor Canvas"): a full race's `gap_updated` stream
 * can reach the low thousands of samples per driver across ~20 cars, and
 * diffing that many SVG path points on every tick would cost far more than
 * one Canvas clear-and-redraw.
 *
 * Bands are drawn as thick, translucent team-colored strokes rather than a
 * literal stacked streamgraph: a stacked streamgraph decomposes a whole
 * into parts, but gap-to-leader per driver is not a decomposition of
 * anything (each driver's gap is independent), so stacking would
 * misrepresent the data. A thick line reads as a flowing "band" and makes
 * parallel/crossing runs (DRS trains, undercut swings) visible as shapes —
 * which is what this chart is actually asked for. */
import type { GapStreamFrame } from './gap-stream-frame';

const PADDING = { top: 10, right: 12, bottom: 26, left: 44 };
const BAND_WIDTH_PX = 3;
const BAND_ALPHA = 0.82;
const DIMMED_ALPHA_FACTOR = 0.22;
const GRID_COLOR = 'rgba(148, 140, 125, 0.3)';
const LABEL_COLOR = '#948c7d';
const LABEL_FONT = '10px ui-monospace, Menlo, Consolas, monospace';
const CODE_FONT = '700 10px ui-monospace, Menlo, Consolas, monospace';

export class GapStreamRenderer {
	private width = 0;
	private height = 0;
	private dpr = 1;

	constructor(private readonly ctx: CanvasRenderingContext2D) {}

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

	draw(frame: GapStreamFrame, highlightDriver: string | null = null): void {
		const { ctx, width, height, dpr } = this;
		ctx.save();
		ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
		ctx.clearRect(0, 0, width, height);

		const maxLap = Math.max(frame.maxLap, 1);
		const maxGapS = Math.max(frame.maxGapS, 5);
		const plotW = Math.max(width - PADDING.left - PADDING.right, 1);
		const plotH = Math.max(height - PADDING.top - PADDING.bottom, 1);
		const xFor = (lap: number): number => PADDING.left + (lap / maxLap) * plotW;
		const yFor = (gapS: number): number => PADDING.top + (gapS / maxGapS) * plotH; // 0 (leader) at top

		this._drawAxes(xFor, yFor, maxLap, maxGapS, plotW, plotH);
		for (const band of frame.bands) {
			this._drawBand(band, xFor, yFor, highlightDriver === null || highlightDriver === band.driver);
		}

		ctx.restore();
	}

	private _drawAxes(
		xFor: (lap: number) => number,
		yFor: (gapS: number) => number,
		maxLap: number,
		maxGapS: number,
		plotW: number,
		plotH: number
	): void {
		const { ctx } = this;
		ctx.strokeStyle = GRID_COLOR;
		ctx.fillStyle = LABEL_COLOR;
		ctx.font = LABEL_FONT;
		ctx.lineWidth = 1;

		const gapStep = niceStep(maxGapS, 4);
		for (let g = 0; g <= maxGapS + 1e-6; g += gapStep) {
			const y = yFor(g);
			ctx.beginPath();
			ctx.moveTo(PADDING.left, y);
			ctx.lineTo(PADDING.left + plotW, y);
			ctx.stroke();
			ctx.fillText(`+${g.toFixed(0)}s`, 2, y + 3);
		}

		const lapStep = niceStep(maxLap, 6);
		ctx.textAlign = 'center';
		for (let lap = 0; lap <= maxLap; lap += lapStep) {
			ctx.fillText(String(lap), xFor(lap), PADDING.top + plotH + 16);
		}
		ctx.textAlign = 'left';
	}

	private _drawBand(
		band: GapStreamFrame['bands'][number],
		xFor: (lap: number) => number,
		yFor: (gapS: number) => number,
		highlighted: boolean
	): void {
		const { ctx } = this;
		ctx.save();
		ctx.globalAlpha = highlighted ? BAND_ALPHA : BAND_ALPHA * DIMMED_ALPHA_FACTOR;
		ctx.strokeStyle = band.color;
		ctx.lineWidth = highlighted ? BAND_WIDTH_PX * 1.4 : BAND_WIDTH_PX;
		ctx.lineJoin = 'round';
		ctx.lineCap = 'round';
		ctx.beginPath();
		band.points.forEach((point, index) => {
			const x = xFor(point.lap);
			const y = yFor(point.gapS);
			if (index === 0) ctx.moveTo(x, y);
			else ctx.lineTo(x, y);
		});
		ctx.stroke();
		ctx.restore();

		const last = band.points[band.points.length - 1];
		if (last && band.code && highlighted) {
			ctx.save();
			ctx.font = CODE_FONT;
			ctx.fillStyle = band.color;
			ctx.fillText(band.code, xFor(last.lap) + 6, yFor(last.gapS) + 3);
			ctx.restore();
		}
	}
}

function niceStep(max: number, targetTicks: number): number {
	const raw = max / targetTicks;
	if (raw <= 0) return 1;
	const magnitude = 10 ** Math.floor(Math.log10(raw));
	const residual = raw / magnitude;
	const step = residual >= 5 ? 5 : residual >= 2 ? 2 : 1;
	return step * magnitude;
}
