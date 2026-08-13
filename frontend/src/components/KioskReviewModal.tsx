import React from 'react';
import { Server } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';
import type { PendingKiosk } from '../hooks/usePendingKiosks';

/**
 * The administrator's view of one pending kiosk request.
 *
 * Approving grants a machine access to the fleet's backups, so the details the
 * kiosk supplied — name, phone, comment — are shown in full: they are the only
 * evidence of who is asking. The UUID is blank until the kiosk completes its
 * handshake, hence the `PENDING-` placeholder.
 */

export interface KioskReviewModalProps {
  kiosk: PendingKiosk;
  onApprove: (id: number) => void;
  onReject: (id: number) => void;
  onClose: () => void;
}

export default function KioskReviewModal({ kiosk, onApprove, onReject, onClose }: KioskReviewModalProps) {
  const { t } = useTranslation();

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fade-in">
      <div className="w-full max-w-md p-6 bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl space-y-4 animate-modal-in">
        <div className="flex items-center gap-3 border-b border-zinc-800 pb-3">
          <div className="p-2 bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 rounded-lg">
            <Server size={20} className="animate-pulse" />
          </div>
          <div>
            <h3 className="text-base font-bold text-zinc-50 leading-tight">
              {t('enrollmentModalTitle') || 'Pending Kiosk Connection Request'}
            </h3>
            <p className="text-[10px] text-zinc-400 font-semibold uppercase tracking-wider">
              {kiosk.name || t('unnamedKiosk') || 'Unnamed Kiosk'}
            </p>
          </div>
        </div>

        <div className="space-y-3 text-xs border-b border-zinc-850 pb-3">
          <div className="grid grid-cols-3 gap-2">
            <span className="text-zinc-500 font-semibold">{t('kioskPhone') || 'Phone'}:</span>
            <span className="col-span-2 text-zinc-300 font-medium">{kiosk.phone || '—'}</span>
          </div>
          <div className="grid grid-cols-3 gap-2">
            <span className="text-zinc-500 font-semibold">{t('kioskComment') || 'Comment'}:</span>
            <span className="col-span-2 text-zinc-300 font-medium whitespace-pre-wrap">{kiosk.comment || '—'}</span>
          </div>
          <div className="grid grid-cols-3 gap-2">
            <span className="text-zinc-500 font-semibold">UUID:</span>
            <span className="col-span-2 text-zinc-400 font-mono select-all break-all">
              {kiosk.uuid.startsWith('PENDING-') ? (
                <span className="text-zinc-500 italic">{t('kioskPending') || 'Pending...'}</span>
              ) : (
                kiosk.uuid
              )}
            </span>
          </div>
        </div>

        <div className="bg-zinc-950 p-4 border border-zinc-850 rounded-xl space-y-2 text-center text-zinc-400">
          <p className="text-xs">
            {t('kioskApprovePrompt') || 'This kiosk is requesting connection. Click "Activate" to grant access.'}
          </p>
        </div>

        <div className="flex justify-end gap-2 pt-2 border-t border-zinc-800">
          <button
            type="button"
            onClick={() => onReject(kiosk.id)}
            className="px-4 py-2 text-xs font-semibold text-rose-400 bg-rose-950/20 hover:bg-rose-950/40 border border-rose-900/30 rounded-lg transition-colors cursor-pointer"
          >
            {t('rejectKiosk') || 'Reject Kiosk'}
          </button>
          <button
            type="button"
            onClick={() => onApprove(kiosk.id)}
            className="px-4 py-2 text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors cursor-pointer"
          >
            {t('kioskActionEnable') || 'Approve & Activate'}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-xs font-semibold text-zinc-300 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors cursor-pointer"
          >
            {t('closeButton') || 'Close'}
          </button>
        </div>
      </div>
    </div>
  );
}
