import { sveltekit } from '@sveltejs/kit/vite';
// vitest's `defineConfig` (not plain vite's) is what types the `test` field
// below — it merges vite's UserConfig with vitest's own, so one config file
// covers both `vite dev`/`vite build` and `vitest run`.
import { defineConfig } from 'vitest/config';

export default defineConfig({
	plugins: [sveltekit()],
	server: {
		port: 5173,
		strictPort: false
	},
	// Pre-bundle the one runtime npm dependency at cold start rather than
	// discovering it lazily on first use. Without this, the FIRST page that
	// happens to pull in a not-yet-optimized module (e.g. the race dashboard,
	// the first route to mount every kernel-analytics view at once) triggers
	// Vite's dev server to re-optimize mid-request and push a full-reload
	// over its HMR socket — which aborts and duplicates whatever requests
	// were in flight at that exact moment. Harmless in ad hoc `just web-dev`
	// browsing, but exactly the kind of one-time hiccup that makes an
	// automated E2E run (`just verify-phase3`) flaky the first time a given
	// route is visited in a fresh dev-server process.
	optimizeDeps: {
		include: ['openapi-fetch']
	},
	test: {
		// The Delay Sync core (src/lib/delay-sync/) is deliberately plain,
		// framework-agnostic TS with no DOM dependency — its spoiler-safety
		// guarantee has to be provable without a browser, so 'node' is enough.
		environment: 'node',
		include: ['src/**/*.test.ts']
	}
});
