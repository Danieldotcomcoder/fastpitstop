const TYRE_VAR: Record<string, string> = {
	soft: '--tyre-soft',
	medium: '--tyre-medium',
	hard: '--tyre-hard',
	intermediate: '--tyre-inter',
	wet: '--tyre-wet'
};

export function tyreColorVar(compound: string | null): string {
	if (compound === null) return '--text-muted';
	return TYRE_VAR[compound.toLowerCase()] ?? '--text-muted';
}

export function tyreLabel(compound: string | null): string {
	if (compound === null) return 'Unknown';
	return compound.charAt(0).toUpperCase() + compound.slice(1);
}
