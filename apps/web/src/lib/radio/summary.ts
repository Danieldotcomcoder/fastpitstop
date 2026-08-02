/** Pure summary logic for a session's radio corpus: how many clips exist vs
 * how many actually have a transcript yet. Exists so the UI can tell "the
 * batch hasn't run for this weekend" (0 transcribed) apart from "these
 * specific clips are still pending" (some transcribed) — collapsing the
 * former into one honest line instead of repeating "Transcription pending."
 * once per clip, which reads as broken rather than as an honest gap. */
export interface RadioClipLike {
	hasTranscript: boolean;
}

export interface RadioCorpusSummary {
	total: number;
	transcribed: number;
}

export function summarizeRadioCorpus(clips: RadioClipLike[]): RadioCorpusSummary {
	return {
		total: clips.length,
		transcribed: clips.filter((c) => c.hasTranscript).length
	};
}
