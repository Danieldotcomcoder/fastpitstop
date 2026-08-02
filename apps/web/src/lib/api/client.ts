/** The typed REST client — generated from `python/f1_api/openapi.json` via
 * `pnpm generate-client` (`just web-generate-client` at the repo root). A
 * backend response-model change regenerates `schema.d.ts` and any call site
 * relying on a field that moved or disappeared fails to typecheck, per this
 * prompt's acceptance criterion — that's the whole point of generating this
 * rather than hand-typing the routes. */
import createClient from 'openapi-fetch';
import type { paths } from './schema';
import { apiBaseUrl } from './config';

export const api = createClient<paths>({ baseUrl: apiBaseUrl() });

export type { paths } from './schema';
export type { components } from './schema';
