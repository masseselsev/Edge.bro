import { useCallback, useState } from 'react';
import { api } from '../api';
import { usePolledResource } from './usePolledResource';
import { useTranslation } from '../context/TranslationContext';

/**
 * The orchestrator's side of kiosk pairing: requests waiting for approval.
 *
 * A kiosk that enrolls has no way to tell anyone — it may be on a technician's
 * bench, behind NAT, unreachable from here. So the orchestrator polls for
 * pending requests and raises a banner. Ten seconds is chosen for the human on
 * the phone waiting to be let in, not for the data.
 *
 * Approve and reject both re-read the list rather than editing it locally: a
 * second administrator may have acted on the same queue, and the banner showing
 * a request that no longer exists is worse than a redundant request.
 *
 * The `kiosks-updated` event exists because the Live-CD tab keeps its own copy
 * of the kiosk list and is not mounted when the banner is used.
 */

export interface PendingKiosk {
  id: number;
  kiosk_id?: string;
  uuid?: string;
  name?: string | null;
  contact?: string | null;
  phone?: string | null;
  comment?: string | null;
  key?: string;
  status: string;
}

export function usePendingKiosks(enabled: boolean) {
  const { t } = useTranslation();
  const [pending, setPending] = useState<PendingKiosk[]>([]);
  const [activeReview, setActiveReview] = useState<PendingKiosk | null>(null);

  const { refresh } = usePolledResource<PendingKiosk[]>('/api/kiosks', 10000, {
    enabled,
    transform: (all: any[]) =>
      (all || [])
        .filter((k: any) => k.status === 'PENDING')
        .map((k: any) => ({
          ...k,
          kiosk_id: k.kiosk_id || k.uuid || '',
          uuid: k.kiosk_id || k.uuid || '',
          contact: k.contact || k.phone || '',
          phone: k.contact || k.phone || '',
        })),
    onData: setPending,
    onError: (err) => console.error('Failed to fetch pending kiosks:', err),
  });

  const settle = useCallback(async (action: () => Promise<unknown>, failure: string) => {
    try {
      await action();
      await refresh();
      setActiveReview(null);
      window.dispatchEvent(new CustomEvent('kiosks-updated'));
    } catch (err: any) {
      alert(err?.message || failure);
    }
  }, [refresh]);

  const approve = useCallback(
    (id: number) => settle(() => api.post(`/api/kiosks/${id}/toggle-active`), 'Failed to approve kiosk'),
    [settle]
  );

  const reject = useCallback(
    async (id: number) => {
      // Rejection deletes the kiosk record, so it is confirmed. Keep this here
      // rather than at the call site: the destructive step and its guard belong
      // together, and there is already more than one button that reaches it.
      if (!window.confirm(t('kioskRevokeConfirm') || 'Are you sure you want to reject this request?')) {
        return;
      }
      await settle(() => api.del(`/api/kiosks/${id}`), 'Failed to reject request');
    },
    [settle, t]
  );

  return { pending, activeReview, setActiveReview, approve, reject };
}
