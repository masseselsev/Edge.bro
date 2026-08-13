import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api';

/**
 * Poll an endpoint on an interval. One implementation, seven call sites.
 *
 * App.tsx grew seven copies of the same twenty-line skeleton — fetch, check
 * `res.ok`, parse, setState, `setInterval`, return a `clearInterval` — and they
 * had drifted. Some fetched immediately and some waited a full interval; some
 * logged failures and some swallowed them; none guarded against a response
 * arriving after the component stopped caring about it.
 *
 * ## The callback-identity problem
 *
 * The obvious way to write this hook is wrong. Putting `transform`/`onData` in
 * the effect's dependency array tears the interval down and rebuilds it on
 * every render, because callers pass inline arrow functions whose identity
 * changes each time — so a 3-second poll fires on every keystroke elsewhere in
 * the tree. Putting them in the array *and* asking callers to `useCallback`
 * everything pushes the problem onto seven call sites, where it will be got
 * wrong once and be invisible when it is.
 *
 * So the callbacks live in refs that are refreshed on every render, and the
 * effect depends only on the things that genuinely define the schedule: url,
 * interval, and enabled. Callers may pass whatever closures they like, and the
 * poll always calls the newest ones.
 *
 * ## Stale responses
 *
 * A request in flight when `url` changes or the component unmounts is not
 * cancelled — it is ignored. React will warn about setState after unmount
 * otherwise, and more importantly a slow response from a previous url would
 * otherwise overwrite the current one's data.
 */
export interface PolledResourceOptions<T> {
  /** When false, nothing is fetched and no interval runs. Default true. */
  enabled?: boolean;
  /**
   * Fetch once immediately as well as on the interval. Default true.
   * Set false where the first value was already loaded elsewhere — the kiosk
   * boot sequence pre-fetches network status, and re-requesting it on mount
   * would be a redundant round trip during the slowest part of startup.
   */
  immediate?: boolean;
  /** Pick the piece of the response worth keeping. Defaults to the whole body. */
  transform?: (raw: any) => T;
  /** Side effects on a successful poll. Sees the latest render's closure. */
  onData?: (value: T) => void;
  /** Failures are otherwise silent — the previous value simply stays. */
  onError?: (error: unknown) => void;
}

export interface PolledResource<T> {
  /** Latest successful value, or null before the first one arrives. */
  data: T | null;
  /**
   * Whether the last attempt succeeded: null before the first attempt, then
   * true/false. Distinct from `data !== null` because an endpoint polled only
   * to find out whether it answers at all has no body worth keeping.
   */
  reachable: boolean | null;
  /** Fetch now, out of band. Does not reset the interval. */
  refresh: () => Promise<void>;
}

export function usePolledResource<T = any>(
  url: string,
  intervalMs: number,
  options: PolledResourceOptions<T> = {}
): PolledResource<T> {
  const { enabled = true, immediate = true } = options;

  const [data, setData] = useState<T | null>(null);
  const [reachable, setReachable] = useState<boolean | null>(null);

  // Refreshed every render; read at poll time. See the note above on why these
  // are not effect dependencies.
  const optionsRef = useRef(options);
  optionsRef.current = options;

  // Bumped by the effect's cleanup. A request captures the generation it was
  // issued under and drops its result if that has moved on — which covers both
  // unmount and a change of url, where a plain boolean would not: the new
  // effect re-arms the flag before the old request resolves.
  const generationRef = useRef(0);

  const fetchOnce = useCallback(async () => {
    const generation = generationRef.current;
    try {
      const raw = await api.get<any>(url);
      if (generationRef.current !== generation) return;
      const { transform, onData } = optionsRef.current;
      const value = (transform ? transform(raw) : raw) as T;
      setData(value);
      setReachable(true);
      onData?.(value);
    } catch (error) {
      if (generationRef.current !== generation) return;
      setReachable(false);
      optionsRef.current.onError?.(error);
    }
  }, [url]);

  useEffect(() => {
    if (!enabled) return;

    if (immediate) void fetchOnce();

    const interval = setInterval(() => { void fetchOnce(); }, intervalMs);
    return () => {
      generationRef.current += 1;
      clearInterval(interval);
    };
  }, [enabled, immediate, intervalMs, fetchOnce]);

  return { data, reachable, refresh: fetchOnce };
}
