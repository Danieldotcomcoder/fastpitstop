import { describe, expect, it } from 'vitest';
import { summarizeRadioCorpus } from './summary';

describe('summarizeRadioCorpus', () => {
	it('counts total and transcribed separately', () => {
		const clips = [{ hasTranscript: true }, { hasTranscript: false }, { hasTranscript: false }];
		expect(summarizeRadioCorpus(clips)).toEqual({ total: 3, transcribed: 1 });
	});

	it('handles an empty corpus', () => {
		expect(summarizeRadioCorpus([])).toEqual({ total: 0, transcribed: 0 });
	});

	it('handles a fully-untranscribed corpus', () => {
		const clips = [{ hasTranscript: false }, { hasTranscript: false }];
		expect(summarizeRadioCorpus(clips)).toEqual({ total: 2, transcribed: 0 });
	});
});
