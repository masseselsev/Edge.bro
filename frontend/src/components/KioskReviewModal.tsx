import React from 'react';
import { Server, ShieldAlert, Key, Phone, MessageSquare, Fingerprint } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';
import type { PendingKiosk } from '../hooks/usePendingKiosks';

/**
 * The administrator's view of one pending kiosk request.
 *
 * Approving grants a machine access to the fleet's backups, so the details the
 * kiosk supplied — name, contact, comment — are shown in full: they are the only
 * evidence of who is asking.
 */

export interface KioskReviewModalProps {
  kiosk: PendingKiosk;
  onApprove: (id: number) => void;
  onReject: (id: number) => void;
  onClose: () => void;
}

export default function KioskReviewModal({ kiosk, onApprove, onReject, onClose }: KioskReviewModalProps) {
  const { t } = useTranslation();
  const kioskIdentifier = kiosk.kiosk_id || kiosk.uuid || '';
  const isPendingId = !kioskIdentifier || kioskIdentifier.startsWith('PENDING-');
  const contact = kiosk.contact || kiosk.phone;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fade-in">
      <div className="w-full max-w-md p-6 bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded-2xl shadow-2xl space-y-4 animate-modal-in">
        <div className="flex items-center gap-3 border-b border-zinc-200 dark:border-zinc-800 pb-3">
          <div className="p-2.5 bg-indigo-50 dark:bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border border-indigo-200 dark:border-indigo-500/20 rounded-xl">
            <Server size={20} className="animate-pulse" />
          </div>
          <div>
            <h3 className="text-base font-bold text-zinc-900 dark:text-zinc-50 leading-tight">
              {t('enrollmentModalTitle') || 'Pending Kiosk Connection Request'}
            </h3>
            <p className="text-[11px] text-zinc-500 dark:text-zinc-400 font-semibold uppercase tracking-wider mt-0.5">
              {kiosk.name || t('unnamedKiosk') || 'Unnamed Kiosk'}
            </p>
          </div>
        </div>

        <div className="space-y-3 text-xs border-b border-zinc-200 dark:border-zinc-800 pb-3">
          <div className="grid grid-cols-3 gap-2 items-center">
            <span className="flex items-center gap-1.5 text-zinc-500 dark:text-zinc-400 font-semibold">
              <Phone size={13} className="shrink-0" />
              {t('kioskContact') || 'Contact'}:
            </span>
            <span className="col-span-2 text-zinc-900 dark:text-zinc-200 font-medium">{contact || '—'}</span>
          </div>
          <div className="grid grid-cols-3 gap-2 items-start">
            <span className="flex items-center gap-1.5 text-zinc-500 dark:text-zinc-400 font-semibold">
              <MessageSquare size={13} className="shrink-0 mt-0.5" />
              {t('kioskComment') || 'Comment'}:
            </span>
            <span className="col-span-2 text-zinc-900 dark:text-zinc-200 font-medium whitespace-pre-wrap">{kiosk.comment || '—'}</span>
          </div>
          <div className="grid grid-cols-3 gap-2 items-center">
            <span className="flex items-center gap-1.5 text-zinc-500 dark:text-zinc-400 font-semibold">
              <Fingerprint size={13} className="shrink-0" />
              {t('kioskUuidLabel') || 'Kiosk ID'}:
            </span>
            <span className="col-span-2 text-zinc-700 dark:text-zinc-300 font-mono select-all break-all text-[11px]">
              {isPendingId ? (
                <span className="text-zinc-400 dark:text-zinc-500 italic">{t('kioskPending') || 'Pending handshake...'}</span>
              ) : (
                kioskIdentifier
              )}
            </span>
          </div>
          {kiosk.key && (
            <div className="grid grid-cols-3 gap-2 items-center">
              <span className="flex items-center gap-1.5 text-zinc-500 dark:text-zinc-400 font-semibold">
                <Key size={13} className="shrink-0" />
                {t('kioskPairingKeyTooltip') || 'Pairing Key'}:
              </span>
              <span className="col-span-2 text-amber-600 dark:text-amber-400 font-mono font-bold select-all tracking-wider text-sm">
                {kiosk.key}
              </span>
            </div>
          )}
        </div>

        <div className="bg-zinc-50 dark:bg-zinc-950 p-3.5 border border-zinc-200 dark:border-zinc-800 rounded-xl text-center text-zinc-600 dark:text-zinc-400">
          <p className="text-xs leading-relaxed">
            {t('kioskApprovePrompt') || 'This kiosk is requesting connection. Click "Approve & Activate" to grant access.'}
          </p>
        </div>

        <div className="flex justify-end gap-2.5 pt-2">
          <button
            type="button"
            onClick={() => onReject(kiosk.id)}
            className="px-3.5 py-2 text-xs font-semibold text-rose-600 dark:text-rose-400 bg-rose-50 hover:bg-rose-100 dark:bg-rose-950/20 dark:hover:bg-rose-950/40 border border-rose-200 dark:border-rose-900/30 rounded-lg transition-colors cursor-pointer"
          >
            {t('rejectKiosk') || 'Reject Kiosk'}
          </button>
          <button
            type="button"
            onClick={() => onApprove(kiosk.id)}
            className="px-4 py-2 text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-all cursor-pointer shadow-sm hover:shadow-indigo-500/20 active:scale-95"
          >
            {t('approveKiosk') || t('kioskActionEnable') || 'Approve & Activate'}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="px-3.5 py-2 text-xs font-semibold text-zinc-700 dark:text-zinc-300 bg-zinc-100 hover:bg-zinc-200 dark:bg-zinc-800/50 dark:hover:bg-zinc-800 rounded-lg transition-colors cursor-pointer"
          >
            {t('closeButton') || 'Close'}
          </button>
        </div>
      </div>
    </div>
  );
}
