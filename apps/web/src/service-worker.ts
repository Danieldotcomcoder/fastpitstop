/// <reference types="@sveltejs/kit" />
/// <reference no-default-lib="true"/>
/// <reference lib="esnext" />
/// <reference lib="webworker" />

/** The installable app's offline shell (ROADMAP W7). It keeps the app's
 * own code and static files so an installed FastPitStop opens instantly,
 * and shows `offline.html` when a page is asked for with no connection.
 *
 * It never stores race data. Timing, the stream and every API response go
 * to the network as before: a cached answer would be stale at best, and a
 * spoiler at worst if it held more of a race than the viewer has reached.
 * Pages themselves are always fetched fresh too, so a deploy is seen on the
 * next visit; their hashed assets live in a cache named for the build. */

import { build, files, version } from '$service-worker';

const sw = self as unknown as ServiceWorkerGlobalScope;

const CACHE = `shell-${version}`;
const OFFLINE = '/offline.html';
/** Static files the app never shows itself (the social card, robots.txt)
 * stay out of the download. */
const NOT_SHELL = new Set(['/og-image.png', '/robots.txt']);
const SHELL = new Set([...build, ...files.filter((file) => !NOT_SHELL.has(file))]);

sw.addEventListener('install', (event) => {
	event.waitUntil(
		caches
			.open(CACHE)
			.then((cache) => cache.addAll([...SHELL]))
			.then(() => sw.skipWaiting())
	);
});

sw.addEventListener('activate', (event) => {
	event.waitUntil(
		caches
			.keys()
			.then((keys) =>
				Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)))
			)
			.then(() => sw.clients.claim())
	);
});

sw.addEventListener('fetch', (event) => {
	const request = event.request;
	if (request.method !== 'GET') return;
	const url = new URL(request.url);
	if (url.origin !== sw.location.origin) return;

	if (SHELL.has(url.pathname)) {
		event.respondWith(caches.match(url.pathname).then((hit) => hit ?? fetch(request)));
		return;
	}
	if (request.mode === 'navigate') {
		event.respondWith(
			fetch(request).catch(async () => (await caches.match(OFFLINE)) ?? Response.error())
		);
	}
});
