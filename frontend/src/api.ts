/**
 * The one place that knows what an API failure looks like.
 *
 * There are ~150 `fetch('/api/...')` calls in this app and, before this file,
 * exactly none of them checked for 401. Sessions expire. When one did, every
 * panel that polls quietly resolved to an empty list and the UI showed an
 * operator a fleet with no nodes, a history with no backups, and no hint that
 * anything had gone wrong — indistinguishable from a working install with
 * nothing in it. That is the worst possible failure for a backup console.
 *
 * Two mechanisms, because there are two problems:
 *
 * 1. `installApiErrorHandling` wraps `window.fetch` once at startup so a 401
 *    from any of those call sites — including the ones not yet migrated —
 *    puts the login screen back up. Patching the global is a blunt instrument
 *    and deliberately chosen over editing 150 call sites at once; it changes
 *    nothing about the response the caller receives.
 * 2. `api.get`/`api.post`/... are for new code and for call sites as they are
 *    touched. They check `res.ok`, parse the body, and throw `ApiError`, so a
 *    failure surfaces as a rejected promise rather than as `undefined`
 *    flowing into a setState.
 */

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

type UnauthorizedHandler = () => void;

let unauthorizedHandler: UnauthorizedHandler | null = null;
let installed = false;

/**
 * Endpoints where a 401 is an answer, not a session expiry.
 *
 * `/api/auth/me` is the probe that *asks* whether there is a session, and the
 * login form's own 401 means "wrong password" — bouncing either to the login
 * screen would be a redirect loop in the first case and a lost error message
 * in the second.
 */
const EXPECTED_401 = ['/api/auth/me', '/api/auth/login', '/api/version'];

function urlPath(input: RequestInfo | URL): string {
  try {
    if (typeof input === 'string') return new URL(input, window.location.origin).pathname;
    if (input instanceof URL) return input.pathname;
    return new URL(input.url, window.location.origin).pathname;
  } catch {
    return '';
  }
}

/**
 * Route every 401 from an API call to `onUnauthorized`, once.
 *
 * Idempotent: calling it twice would otherwise nest the wrapper and fire the
 * handler once per layer.
 */
export function installApiErrorHandling(onUnauthorized: UnauthorizedHandler): void {
  unauthorizedHandler = onUnauthorized;
  if (installed) return;
  installed = true;

  const original = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const response = await original(input, init);
    if (response.status === 401) {
      const path = urlPath(input);
      if (path.startsWith('/api/') && !EXPECTED_401.includes(path)) {
        unauthorizedHandler?.();
      }
    }
    return response;
  };
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method };
  if (body !== undefined) {
    init.headers = { 'Content-Type': 'application/json' };
    init.body = JSON.stringify(body);
  }

  const response = await fetch(path, init);

  if (!response.ok) {
    // FastAPI puts the human-readable reason in `detail`; fall back to the
    // status text so the thrown error is never just "Error".
    let detail: unknown;
    let message = `${response.status} ${response.statusText}`;
    try {
      detail = await response.json();
      if (detail && typeof detail === 'object' && 'detail' in detail) {
        const value = (detail as { detail: unknown }).detail;
        if (typeof value === 'string') message = value;
      }
    } catch {
      // A non-JSON error body (a proxy's HTML 502 page, say) is not worth
      // reporting beyond the status.
    }
    throw new ApiError(response.status, message, detail);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
  put: <T>(path: string, body?: unknown) => request<T>('PUT', path, body),
  patch: <T>(path: string, body?: unknown) => request<T>('PATCH', path, body),
  del: <T>(path: string, body?: unknown) => request<T>('DELETE', path, body),
};
