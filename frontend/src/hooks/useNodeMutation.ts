import { useCallback, useState } from 'react';
import { api } from '../api';

/**
 * POST something to a node and refresh what is on screen.
 *
 * NodeDetailsModal had nine of these written out longhand — notes, NAT
 * override, rate limit, group assignment, pause, backup-today, reprovision,
 * licence, revoke — each fifteen lines of fetch, `res.ok`, refresh,
 * `console.error`, `finally`. The differences between them were three lines
 * each and were buried in the repetition.
 *
 * `run` resolves to whether the server accepted it, so a caller that changed
 * the UI optimistically can put it back. Several of them did not, which is how
 * a failed group assignment left the modal showing a group the node was never
 * moved to.
 */

export interface NodeMutation {
  /** True while any mutation from this hook is in flight. */
  pending: boolean;
  /**
   * POST to `path` and, on success, run the refresh callback.
   * Resolves false on any failure — the error is logged, not thrown, because
   * every call site here is a button that should re-enable rather than a flow
   * that should stop.
   */
  run: (path: string, body?: unknown) => Promise<boolean>;
}

export function useNodeMutation(onSuccess: () => void): NodeMutation {
  const [pending, setPending] = useState(false);

  const run = useCallback(async (path: string, body?: unknown): Promise<boolean> => {
    setPending(true);
    try {
      await api.post(path, body);
      onSuccess();
      return true;
    } catch (err) {
      console.error(err);
      return false;
    } finally {
      setPending(false);
    }
  }, [onSuccess]);

  return { pending, run };
}
