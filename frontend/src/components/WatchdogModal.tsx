import React from 'react';
import { ShieldAlert, RefreshCw } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';

interface WatchdogModalProps {
  onClose: () => void;
  onFreeze: () => void;
  watchdogStatus: any;
  watchdogActionLoading: boolean;
}

export default function WatchdogModal({
  onClose,
  onFreeze,
  watchdogStatus,
  watchdogActionLoading
}: WatchdogModalProps) {
  const { t } = useTranslation();

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fade-in">
      <div className="w-full max-w-md p-6 bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl space-y-4 animate-modal-in">
        <div className="flex items-start gap-3 border-b border-zinc-800 pb-3">
          <div className="p-2 bg-rose-500/10 text-rose-400 border border-rose-500/20 rounded-lg shrink-0">
            <ShieldAlert size={20} className="animate-pulse" />
          </div>
          <div>
            <h3 className="text-base font-bold text-zinc-50 leading-tight">{t('watchdogTitle')}</h3>
            <p className="text-[10px] text-zinc-500 font-semibold uppercase tracking-wider mt-0.5">{watchdogStatus?.port}</p>
          </div>
        </div>
        <p className="text-xs text-zinc-300 leading-relaxed">
          {t('watchdogAlertText')}
        </p>
        <div className="flex justify-end gap-2 pt-2 border-t border-zinc-800">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-xs font-bold text-zinc-400 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors cursor-pointer"
          >
            {t('closeButton') || 'Close'}
          </button>
          <button
            type="button"
            onClick={onFreeze}
            disabled={watchdogActionLoading}
            className="px-4 py-2 text-xs font-bold text-white bg-rose-600 hover:bg-rose-500 rounded-lg disabled:opacity-50 transition-colors flex items-center gap-1.5 cursor-pointer"
          >
            {watchdogActionLoading ? <RefreshCw size={12} className="animate-spin" /> : null}
            {t('watchdogFreezeButton')}
          </button>
        </div>
      </div>
    </div>
  );
}
