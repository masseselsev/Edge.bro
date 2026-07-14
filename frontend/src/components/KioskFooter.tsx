import React from 'react';
import { Loader2, ShieldAlert, Link2, AlertTriangle, RefreshCw } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';

interface KioskFooterProps {
  restoreMode: string;
  kioskStatus: string;
  activationMsg: string;
  activationError: string;
  handleRequestActivation: () => void;
  requestingActivation: boolean;
  setPairingIp: (ip: string) => void;
  setPairingKey: (key: string) => void;
  setPairingError: (err: string) => void;
  setPairingSuccess: (msg: string) => void;
  setShowPairingModal: (open: boolean) => void;
  kioskOrchestratorIp: string;
  kioskId: string;
  connectionKeyphrase: string;
  watchdogStatus: any;
  watchdogActionLoading: boolean;
  handleUnfreezeWatchdog: () => void;
  handleFreezeWatchdog: () => void;
  healthWarnings: any[];
}

export default function KioskFooter({
  restoreMode,
  kioskStatus,
  activationMsg,
  activationError,
  handleRequestActivation,
  requestingActivation,
  setPairingIp,
  setPairingKey,
  setPairingError,
  setPairingSuccess,
  setShowPairingModal,
  kioskOrchestratorIp,
  kioskId,
  connectionKeyphrase,
  watchdogStatus,
  watchdogActionLoading,
  handleUnfreezeWatchdog,
  handleFreezeWatchdog,
  healthWarnings
}: KioskFooterProps) {
  const { t } = useTranslation();

  return (
    <footer className="fixed bottom-0 left-0 right-0 z-40 bg-zinc-950/95 backdrop-blur-md border-t border-zinc-900 flex flex-col animate-fade-in">
      {/* Connection / Activation Bar (Horizontal) */}
      {restoreMode === 'online' && kioskStatus !== 'APPROVED' && (
        <div className="px-6 py-2.5 bg-indigo-950/10 border-b border-zinc-900/60 flex flex-wrap items-center justify-between gap-4 text-xs font-semibold">
          <div className="flex items-center gap-2">
            {kioskStatus === 'PENDING' ? (
              <>
                <Loader2 size={13} className="text-indigo-400 animate-spin" />
                <span className="text-indigo-400 font-bold">{t('kioskBlockedPendingTitle') || 'Activation Request Pending'}</span>
                <span className="h-3 w-px bg-zinc-800" />
                <span className="text-[11px] text-zinc-400 font-medium">{t('kioskBlockedPendingSub') || 'Waiting for administrator approval.'}</span>
              </>
            ) : (
              <>
                <ShieldAlert size={13} className="text-red-400" />
                <span className="text-red-400 font-bold">{t('kioskBlockedTitle') || 'Kiosk Access Blocked'}</span>
                <span className="h-3 w-px bg-zinc-800" />
                <span className="text-[11px] text-zinc-400 font-medium">{t('kioskBlockedSub') || 'This kiosk terminal is not authorized. Request activation to connect.'}</span>
              </>
            )}
          </div>
          
          <div className="flex items-center gap-3">
            {activationMsg && <span className="text-emerald-400 text-[11px] font-bold">{activationMsg}</span>}
            {activationError && <span className="text-red-400 text-[11px] font-bold">{activationError}</span>}
            
            {kioskStatus !== 'PENDING' && (
              <button
                type="button"
                onClick={handleRequestActivation}
                disabled={requestingActivation}
                className="px-3 py-1 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded font-bold text-[11px] transition-all cursor-pointer flex items-center gap-1.5 active:translate-y-0.5"
              >
                {requestingActivation && <Loader2 size={11} className="animate-spin" />}
                {t('kioskBlockedRequest') || 'Request Activation'}
              </button>
            )}
            
            <button
              type="button"
              onClick={() => {
                setPairingIp(kioskOrchestratorIp || window.location.hostname);
                setPairingKey('');
                setPairingError('');
                setPairingSuccess('');
                setShowPairingModal(true);
              }}
              className="px-3 py-1 bg-zinc-900 hover:bg-zinc-800 text-zinc-300 rounded border border-zinc-800 text-[11px] font-bold transition-all cursor-pointer flex items-center gap-1 active:translate-y-0.5"
            >
              <Link2 size={11} />
              {t('kioskPairOtherServer') || 'Pair with another server'}
            </button>
          </div>
        </div>
      )}

      {/* Main Footer Info */}
      <div className="py-3 text-center text-xs text-zinc-500 flex flex-wrap items-center justify-center gap-4">
        <span>{t('kioskTitle')}</span>
        <span className="h-4 w-px bg-zinc-800" />
        <span>{t('kioskUuidLabel')}: <span className="font-mono text-zinc-400 select-all font-bold">{kioskId || 'Generating...'}</span></span>
        <span className="h-4 w-px bg-zinc-800" />
        <div className="relative group flex items-center gap-1">
          <span>{t('selectedServer')}</span>
          <span className="text-indigo-400 font-bold border-b border-dashed border-indigo-400/50 cursor-help pb-[1px] hover:text-indigo-300 hover:border-indigo-300 transition-colors">
            {kioskOrchestratorIp || '127.0.0.1'}
          </span>
          {/* Tooltip for hover */}
          <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 hidden group-hover:flex flex-col items-center pointer-events-none z-50">
            <div className="bg-zinc-900 border border-zinc-800 text-zinc-300 text-[10px] py-1.5 px-3 rounded-lg shadow-xl font-mono whitespace-nowrap">
              <span className="text-zinc-500 font-semibold uppercase tracking-wider block text-[8px] mb-0.5 text-center">{t('keyphraseToken')}</span>
              <span className="text-amber-400 font-bold">{connectionKeyphrase || 'unknown'}</span>
            </div>
            <div className="w-2 h-2 bg-zinc-900 border-r border-b border-zinc-800 rotate-45 -mt-1" />
          </div>
        </div>
        {watchdogStatus?.detected && (
          <>
            <span className="h-4 w-px bg-zinc-800" />
            <div className="flex items-center gap-2">
              <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
                watchdogStatus.frozen 
                  ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' 
                  : 'bg-rose-500/10 text-rose-400 border border-rose-500/20 animate-pulse'
              }`}>
                {watchdogStatus.frozen ? t('watchdogFrozenBadge') : t('watchdogActiveBadge')}
                {watchdogStatus.seconds_left !== null && !watchdogStatus.frozen ? ` (${watchdogStatus.seconds_left}s)` : ''}
              </span>
              <button
                type="button"
                disabled={watchdogActionLoading}
                onClick={watchdogStatus.frozen ? handleUnfreezeWatchdog : handleFreezeWatchdog}
                className="px-2.5 py-1 bg-zinc-900 border border-zinc-800 hover:border-zinc-700 text-zinc-200 hover:text-white rounded text-[10px] font-bold transition-all flex items-center gap-1.5 cursor-pointer disabled:opacity-50"
              >
                {watchdogActionLoading && <RefreshCw size={9} className="animate-spin" />}
                {watchdogStatus.frozen ? t('watchdogUnfreezeButton') : t('watchdogFreezeButton')}
              </button>
            </div>
          </>
        )}
      </div>
      {healthWarnings.length > 0 && (
        <div className="px-6 py-1.5 bg-red-950/20 border-t border-zinc-900/60 flex items-center justify-center gap-2 text-[10px] text-red-400 font-bold animate-pulse">
          <AlertTriangle size={12} className="shrink-0" />
          <span>
            {t('warningsCount')} ({healthWarnings.length}):{' '}
            {healthWarnings.map(w =>
              w.code === 'BORG_ON_ROOT' || w.code === 'ISO_CACHE_ON_ROOT'
                ? t('storageRootWarning')
                : w.message
            ).join(' | ')}
          </span>
        </div>
      )}
    </footer>
  );
}
