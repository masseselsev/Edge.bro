import { useCallback, useState } from 'react';
import { api } from '../api';
import { usePolledResource } from './usePolledResource';
import { useTranslation } from '../context/TranslationContext';

/**
 * Everything a kiosk needs to attach itself to an orchestrator.
 *
 * Sixteen `useState` calls in App.tsx belonged to this one flow and were mixed
 * in with the theme, the tab, the fleet and the watchdog. Nothing marked them
 * as a group, so reading any of them meant checking whether the other fifteen
 * were involved.
 *
 * The flow has two entrances, which is why there are two submit handlers:
 *
 * - **Enroll** — the kiosk posts its details and waits for an administrator to
 *   approve it. No secret is exchanged; approval happens on the orchestrator.
 * - **Connect** — the operator was handed a short key out of band and types it
 *   in, pairing immediately.
 *
 * Both end at the same place: the orchestrator knows this kiosk and
 * `/api/version` starts returning `kiosk_status: APPROVED`.
 *
 * `status` is polled rather than pushed, because approval happens on another
 * machine with no channel back to a kiosk that may not be reachable from it.
 */

export interface KioskVersionPayload {
  orchestrator_ip?: string;
  auth_token?: string;
  kiosk_id?: string;
  available_server_ips?: string[];
  kiosk_status?: string;
}

export type PairingMode = 'enroll' | 'connect';

export function useKioskPairing(isKiosk: boolean) {
  const { t } = useTranslation();

  // Identity, learned at boot from /api/version.
  const [kioskId, setKioskId] = useState('');
  const [orchestratorIp, setOrchestratorIp] = useState('');
  const [connectionKeyphrase, setConnectionKeyphrase] = useState('');
  const [availableServerIps, setAvailableServerIps] = useState<string[]>([]);

  // Approval state. Defaults to APPROVED so an orchestrator-mode browser — which
  // never polls this — is not treated as a blocked kiosk.
  const [status, setStatus] = useState('APPROVED');

  // Modal and form.
  const [showModal, setShowModal] = useState(false);
  const [mode, setMode] = useState<PairingMode>('enroll');
  const [ip, setIp] = useState('');
  const [key, setKey] = useState('');
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [comment, setComment] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [enrollMsg, setEnrollMsg] = useState('');

  // The blocked-kiosk screen's "ask again" button.
  const [requestingActivation, setRequestingActivation] = useState(false);
  const [activationMsg, setActivationMsg] = useState('');
  const [activationError, setActivationError] = useState('');

  usePolledResource<string | undefined>('/api/version', 8000, {
    enabled: isKiosk,
    transform: (data) => data?.kiosk_status,
    onData: (kioskStatus) => {
      if (kioskStatus) setStatus(kioskStatus);
    },
    onError: (err) => console.error('Failed to poll kiosk status:', err),
  });

  /** Seed identity from the boot-time /api/version response. */
  const hydrate = useCallback((data: KioskVersionPayload) => {
    setOrchestratorIp(data.orchestrator_ip || '');
    setConnectionKeyphrase(data.auth_token || '');
    setKioskId(data.kiosk_id || '');
    setAvailableServerIps(data.available_server_ips || []);
    if (data.kiosk_status) setStatus(data.kiosk_status);
  }, []);

  /** Open the modal with the fields cleared and the address pre-filled. */
  const openModal = useCallback(() => {
    setIp(orchestratorIp || window.location.hostname);
    setKey('');
    setError('');
    setSuccess('');
    setShowModal(true);
  }, [orchestratorIp]);

  const closeModal = useCallback(() => setShowModal(false), []);

  const selectMode = useCallback((next: PairingMode) => {
    setMode(next);
    setError('');
    setEnrollMsg('');
  }, []);

  const submitConnect = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError('');
    setSuccess('');
    try {
      await api.post('/api/kiosk/connect', {
        orchestrator_ip: ip.trim(),
        key: key.trim(),
      });

      setSuccess(t('kioskPairingSuccess') || 'Connected and paired successfully!');
      setOrchestratorIp(ip.trim());

      // Held open briefly so the success message is readable, then closed.
      // The keyphrase is re-read because pairing is what mints it — before this
      // point /api/version has nothing to return.
      setTimeout(async () => {
        try {
          const version = await api.get<KioskVersionPayload>('/api/version');
          setConnectionKeyphrase(version.auth_token || '');
        } catch {
          // Not worth surfacing: pairing succeeded, and the keyphrase is a
          // display convenience that the next poll will pick up anyway.
        }
        setShowModal(false);
      }, 1500);
    } catch (err: any) {
      setError(err.message || 'Connection handshake failed');
    } finally {
      setSubmitting(false);
    }
  }, [ip, key, t]);

  const submitEnroll = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError('');
    setEnrollMsg('');
    try {
      await api.post('/api/kiosk/enroll', {
        orchestrator_ip: ip.trim(),
        name: name.trim(),
        phone: phone.trim(),
        comment: comment.trim(),
      });
      setEnrollMsg(
        t('enrollStatusPending') ||
        'Connection request submitted successfully! Waiting for server administrator approval.'
      );
    } catch (err: any) {
      setError(err.message || 'Enrollment request failed');
    } finally {
      setSubmitting(false);
    }
  }, [ip, name, phone, comment, t]);

  const requestActivation = useCallback(async () => {
    setRequestingActivation(true);
    setActivationMsg('');
    setActivationError('');
    try {
      await api.post('/api/kiosk/request-activation');
      setActivationMsg(t('kioskBlockedSuccess') || 'Request submitted successfully!');
      // Reflected locally rather than waiting up to 8s for the next poll to
      // say the same thing.
      setStatus('PENDING');
    } catch (err: any) {
      setActivationError(err.message || t('kioskBlockedError') || 'Failed to submit request.');
    } finally {
      setRequestingActivation(false);
    }
  }, [t]);

  return {
    kioskId,
    orchestratorIp,
    connectionKeyphrase,
    availableServerIps,
    status,
    hydrate,

    showModal,
    openModal,
    closeModal,
    mode,
    selectMode,
    ip,
    setIp,
    key,
    setKey,
    name,
    setName,
    phone,
    setPhone,
    comment,
    setComment,
    submitting,
    error,
    success,
    enrollMsg,
    submitConnect,
    submitEnroll,

    requestingActivation,
    activationMsg,
    activationError,
    requestActivation,
  };
}
