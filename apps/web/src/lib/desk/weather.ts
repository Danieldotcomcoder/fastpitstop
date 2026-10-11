/** The weather strip (ROADMAP W3): the latest `weather_sample` Delay Sync
 * has released, so the conditions shown are the ones on the viewer's TV,
 * not the ones at the track right now. */
import type { Envelope } from '$lib/delay-sync';

export interface Weather {
	airC: number | null;
	trackC: number | null;
	humidityPct: number | null;
	windMs: number | null;
	rain: boolean | null;
}

const num = (value: unknown) =>
	typeof value === 'number' && Number.isFinite(value) ? value : null;

/** The newest released sample, or null before the first. */
export function latestWeather(events: readonly Envelope[]): Weather | null {
	for (let i = events.length - 1; i >= 0; i--) {
		const envelope = events[i];
		if (envelope?.kind !== 'weather_sample') continue;
		const p = envelope.payload;
		return {
			airC: num(p.air_temp),
			trackC: num(p.track_temp),
			humidityPct: num(p.humidity),
			windMs: num(p.wind_speed),
			rain: typeof p.rainfall === 'boolean' ? p.rainfall : null
		};
	}
	return null;
}

/** The strip's readings, in order; unknown values are a dash, never zero. */
export function weatherReadings(weather: Weather | null): { label: string; value: string }[] {
	const deg = (v: number | null) => (v === null ? '—' : `${v.toFixed(1)}°`);
	return [
		{ label: 'Air', value: deg(weather?.airC ?? null) },
		{ label: 'Track', value: deg(weather?.trackC ?? null) },
		{
			label: 'Humidity',
			value: weather?.humidityPct == null ? '—' : `${Math.round(weather.humidityPct)}%`
		},
		{ label: 'Wind', value: weather?.windMs == null ? '—' : `${weather.windMs.toFixed(1)} m/s` },
		{ label: 'Rain', value: weather?.rain == null ? '—' : weather.rain ? 'Yes' : 'No' }
	];
}
