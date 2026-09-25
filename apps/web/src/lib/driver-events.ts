import type { Envelope } from './delay-sync';
import { driverName, type DriverNames } from './drivers';
/** Call only with released envelopes in playback views. */
export function namesFromEvents(events: readonly Envelope[]): DriverNames {
	const names: DriverNames = {};
	for (const e of events) {
		const key = e.payload.session_key as { year?: number } | undefined;
		if (typeof e.payload.driver === 'string' && !names[e.payload.driver]) {
			names[e.payload.driver] = driverName(e.payload.driver, null, null, key?.year);
		}
		if (e.kind !== 'driver_list_updated' || !Array.isArray(e.payload.drivers)) continue;
		for (const ref of e.payload.drivers) {
			if (!ref || typeof ref !== 'object') continue;
			const r = ref as Record<string, unknown>;
			if (typeof r.number !== 'string') continue;
			if (typeof r.full_name === 'string' && r.full_name.trim()) names[r.number] = r.full_name;
			else if (typeof r.code === 'string' || !names[r.number])
				names[r.number] = driverName(
					r.number,
					null,
					typeof r.code === 'string' ? r.code : null,
					key?.year
				);
		}
	}
	return names;
}
