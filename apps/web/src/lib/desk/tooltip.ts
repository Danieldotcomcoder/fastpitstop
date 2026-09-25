/** The design's single floating tooltip, shared by every chart on the desk:
 * one dark box that follows the pointer, a caps heading and label/value
 * rows (with an optional line key in the series colour). */
import { writable } from 'svelte/store';

export interface TipRow {
	label: string;
	value: string;
	color?: string;
	dashed?: boolean;
}

export interface Tip {
	title: string;
	rows: TipRow[];
	x: number;
	y: number;
}

export const tip = writable<Tip | null>(null);

export function showTip(title: string, rows: TipRow[], event: PointerEvent): void {
	tip.set({ title, rows, x: event.clientX, y: event.clientY });
}

export function hideTip(): void {
	tip.set(null);
}
