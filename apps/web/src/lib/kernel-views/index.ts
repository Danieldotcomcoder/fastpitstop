export {
	degradationCrossover,
	degradationCurve,
	type DegradationFitLike
} from './degradation-curve';
export {
	buildGapStreamFrame,
	type GapStreamBand,
	type GapStreamFrame,
	type GapStreamPoint
} from './gap-stream-frame';
export { GapStreamRenderer } from './gap-stream-renderer';
export { applyGapEnvelope, createGapStreamState, GapStreamReducer } from './gap-stream';
export type { DriverGapSeries, GapSample, GapStreamState } from './gap-stream';
export { parseIsoDurationSeconds } from './iso-duration';
export { evalFit, findCrossover, sampleCurve } from './linear-fit';
export type { Crossover, CurveSeries, FitLine } from './linear-fit';
export { applyRevealEnvelope, createRevealState, RevealTracker } from './reveal';
export type { RevealState } from './reveal';
