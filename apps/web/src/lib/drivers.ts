/** Session identities always win. Older API responses may contain only numbers. The fallback is explicitly
 * scoped to the session season, never the current calendar year.
 * 2026 names checked against https://www.formula1.com/en/drivers . */
const NAMES: Record<string, string> = {
	VER: 'Max Verstappen',
	NOR: 'Lando Norris',
	PIA: 'Oscar Piastri',
	HAM: 'Lewis Hamilton',
	LEC: 'Charles Leclerc',
	RUS: 'George Russell',
	ANT: 'Kimi Antonelli',
	HAD: 'Isack Hadjar',
	LAW: 'Liam Lawson',
	LIN: 'Arvid Lindblad',
	GAS: 'Pierre Gasly',
	COL: 'Franco Colapinto',
	OCO: 'Esteban Ocon',
	BEA: 'Oliver Bearman',
	HUL: 'Nico Hülkenberg',
	BOR: 'Gabriel Bortoleto',
	SAI: 'Carlos Sainz',
	ALB: 'Alexander Albon',
	ALO: 'Fernando Alonso',
	STR: 'Lance Stroll',
	PER: 'Sergio Pérez',
	BOT: 'Valtteri Bottas',
	RIC: 'Daniel Ricciardo',
	TSU: 'Yuki Tsunoda',
	ZHO: 'Zhou Guanyu',
	MAG: 'Kevin Magnussen',
	SAR: 'Logan Sargeant',
	DEV: 'Nyck de Vries',
	DOO: 'Jack Doohan'
};
// Official race classifications and 2026 entry list; names only, no inferred team.
// https://www.formula1.com/en/results/2024/races/1229/bahrain/race-result
// https://www.formula1.com/en/results/2025/races/1254/australia/race-result
// https://www.formula1.com/en/latest/article/all-the-2026-f1-driver-numbers-confirmed-in-full.5rh7o9mPntG7NerzVk9onc
const GRID_2024: Record<string, string> = {
	'1': 'VER',
	'2': 'SAR',
	'3': 'RIC',
	'4': 'NOR',
	'10': 'GAS',
	'11': 'PER',
	'14': 'ALO',
	'16': 'LEC',
	'18': 'STR',
	'20': 'MAG',
	'22': 'TSU',
	'23': 'ALB',
	'24': 'ZHO',
	'27': 'HUL',
	'30': 'LAW',
	'31': 'OCO',
	'38': 'BEA',
	'43': 'COL',
	'44': 'HAM',
	'50': 'BEA',
	'55': 'SAI',
	'61': 'DOO',
	'63': 'RUS',
	'77': 'BOT',
	'81': 'PIA'
};
const GRID_2025: Record<string, string> = {
	'1': 'VER',
	'4': 'NOR',
	'5': 'BOR',
	'6': 'HAD',
	'7': 'DOO',
	'10': 'GAS',
	'12': 'ANT',
	'14': 'ALO',
	'16': 'LEC',
	'18': 'STR',
	'22': 'TSU',
	'23': 'ALB',
	'27': 'HUL',
	'30': 'LAW',
	'31': 'OCO',
	'43': 'COL',
	'44': 'HAM',
	'55': 'SAI',
	'63': 'RUS',
	'81': 'PIA',
	'87': 'BEA'
};
const GRID_2026: Record<string, string> = {
	'1': 'NOR',
	'3': 'VER',
	'5': 'BOR',
	'6': 'HAD',
	'10': 'GAS',
	'11': 'PER',
	'12': 'ANT',
	'14': 'ALO',
	'16': 'LEC',
	'18': 'STR',
	'23': 'ALB',
	'27': 'HUL',
	'30': 'LAW',
	'31': 'OCO',
	'41': 'LIN',
	'43': 'COL',
	'44': 'HAM',
	'55': 'SAI',
	'63': 'RUS',
	'77': 'BOT',
	'81': 'PIA',
	'87': 'BEA'
};
const SEASON_GRIDS: Record<number, Record<string, string>> = {
	2024: GRID_2024,
	2025: GRID_2025,
	2026: GRID_2026
};
export function driverName(
	driver: string,
	name?: string | null,
	code?: string | null,
	year?: number
): string {
	const supplied = name?.trim();
	if (supplied) return supplied;
	if (code?.trim() && !/^\d+$/.test(code)) return NAMES[code] ?? code;
	if (NAMES[driver]) return NAMES[driver];
	const seasonCode = year === undefined ? undefined : SEASON_GRIDS[year]?.[driver];
	return (seasonCode && NAMES[seasonCode]) || `Driver ${driver}`;
}
export type DriverNames = Record<string, string>;
export function lapTime(seconds: number | null): string {
	if (seconds === null) return '—';
	const ms = Math.round(seconds * 1000);
	return `${Math.floor(ms / 60000)}:${((ms % 60000) / 1000).toFixed(3).padStart(6, '0')}`;
}
