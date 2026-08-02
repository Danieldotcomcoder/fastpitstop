import adapter from '@sveltejs/adapter-node';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	preprocess: vitePreprocess(),

	kit: {
		// Node server (Prompt 2 of CLAUDE.md's Phase 3: containerised deploy).
		// The app still only ever talks to f1_api over fetch/WebSocket — this
		// Node process serves the built app itself, it never touches Postgres
		// or the kernel directly. Runtime env (`PUBLIC_F1_API_BASE_URL` etc.,
		// via `$env/dynamic/public`) is read per-request, so one built image
		// can be deployed against any environment without a rebuild.
		adapter: adapter({
			out: 'build'
		})
	}
};

export default config;
