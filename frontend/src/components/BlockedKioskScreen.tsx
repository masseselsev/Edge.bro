import React, { useState } from 'react';
import { Loader2, ShieldAlert, Link2 } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';

interface BlockedKioskScreenProps {
  status: string;
  onActivationRequested: () => void;
  onPairingSuccess: () => void;
  appVersion: string;
  kioskId: string;
}

export default function BlockedKioskScreen({ 
  status, 
  onActivationRequested, 
  onPairingSuccess,
  appVersion,
  kioskId
}: BlockedKioskScreenProps) {
  const { t } = useTranslation();
  const [requesting, setRequesting] = useState(false);
  const [msg, setMsg] = useState('');
  const [errorMsg, setErrorMsg] = useState('');

  const [showPairing, setShowPairing] = useState(false);
  const [pairingIp, setPairingIp] = useState('');
  const [pairingKey, setPairingKey] = useState('');
  const [pairingSubmitting, setPairingSubmitting] = useState(false);

  const handleRequest = async () => {
    setRequesting(true);
    setMsg('');
    setErrorMsg('');
    try {
      const res = await fetch('/api/kiosk/request-activation', { method: 'POST' });
      if (res.ok) {
        setMsg(t('kioskBlockedSuccess') || 'Request submitted successfully!');
        onActivationRequested();
      } else {
        const data = await res.json();
        setErrorMsg(data.detail || t('kioskBlockedError') || 'Failed to submit request.');
      }
    } catch (err: any) {
      setErrorMsg(err.message || t('kioskBlockedError') || 'Failed to submit request.');
    } finally {
      setRequesting(false);
    }
  };

  const handlePairingSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setPairingSubmitting(true);
    setMsg('');
    setErrorMsg('');
    try {
      const res = await fetch('/api/kiosk/connect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          orchestrator_ip: pairingIp.trim(),
          key: pairingKey.trim()
        })
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Connection handshake failed');
      }
      setMsg(t('kioskPairingSuccess') || 'Connected and paired successfully!');
      setTimeout(() => {
        onPairingSuccess();
      }, 1500);
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to connect to orchestrator');
    } finally {
      setPairingSubmitting(false);
    }
  };

  return (
    <div className="w-full flex items-center justify-center p-4">
      <div className="max-w-md w-full p-8 bg-zinc-900/50 border border-zinc-800/80 rounded-3xl shadow-2xl space-y-6 text-center animate-fade-in relative overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-b from-indigo-500/5 via-transparent to-transparent pointer-events-none" />

        {/* Status Icon */}
        <div className="flex justify-center">
          {status === 'PENDING' ? (
            <div className="relative p-5 bg-indigo-500/10 border border-indigo-500/20 rounded-2xl animate-pulse">
              <Loader2 size={36} className="text-indigo-400 animate-spin" strokeWidth={2.5} />
            </div>
          ) : (
            <div className="relative p-5 bg-red-500/15 border border-red-500/30 rounded-2xl shadow-lg">
              <ShieldAlert size={36} className="text-red-400 filter drop-shadow-[0_0_8px_rgba(239,68,68,0.5)]" strokeWidth={2} />
            </div>
          )}
        </div>

        {/* Title and Descriptions */}
        <div className="space-y-2">
          <h2 className="text-xl font-black text-zinc-150 tracking-tight">
            {status === 'PENDING' ? t('kioskBlockedPendingTitle') : t('kioskBlockedTitle')}
          </h2>
          <p className="text-sm font-semibold text-zinc-400">
            {status === 'PENDING' ? t('kioskBlockedPendingSub') : t('kioskBlockedSub')}
          </p>
          <p className="text-xs text-zinc-400 leading-relaxed font-medium">
            {status === 'PENDING' 
              ? t('kioskBlockedPendingDesc') 
              : (t('kioskBlockedDesc') || 'Please contact the administrator or request activation below.')
            }
          </p>
        </div>

        {/* Action button / Status messages */}
        <div className="pt-2">
          {!showPairing ? (
            <>
              {status !== 'PENDING' ? (
                <button
                  type="button"
                  onClick={handleRequest}
                  disabled={requesting}
                  className="w-full flex items-center justify-center gap-2 py-3 px-4 bg-gradient-to-r from-indigo-600 to-indigo-700 hover:from-indigo-500 hover:to-indigo-600 text-zinc-50 rounded-xl text-sm font-bold shadow-lg shadow-indigo-600/15 border border-indigo-500/30 transition-all duration-200 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed hover:-translate-y-0.5 active:translate-y-0 active:scale-[0.98]"
                >
                  {requesting ? (
                    <>
                      <Loader2 size={16} className="animate-spin text-zinc-50" />
                      <span>{t('saving') || 'Submitting...'}</span>
                    </>
                  ) : (
                    <span>{t('kioskBlockedRequest')}</span>
                  )}
                </button>
              ) : null}

              <button
                type="button"
                onClick={() => {
                  setPairingIp(window.location.hostname);
                  setPairingKey('');
                  setShowPairing(true);
                }}
                className="mt-4 text-xs font-bold text-indigo-400 hover:text-indigo-300 transition-colors flex items-center justify-center gap-1.5 w-full cursor-pointer"
              >
                <Link2 size={14} />
                {t('kioskPairOtherServer') || 'Pair with another server'}
              </button>
            </>
          ) : (
            <form onSubmit={handlePairingSubmit} className="space-y-4 text-left border-t border-zinc-800 pt-4 mt-2 animate-fade-in">
              <div className="text-xs font-bold text-zinc-300 mb-2 uppercase tracking-wide">
                {t('kioskPairOtherServer') || 'Pair with another server'}
              </div>
              <div>
                <label className="block text-[10px] font-semibold text-zinc-400 mb-1">
                  {t('kioskPairingIpLabel') || 'New Orchestrator IP'}
                </label>
                <input
                  type="text"
                  required
                  placeholder="e.g. 192.168.1.100"
                  value={pairingIp}
                  onChange={(e) => setPairingIp(e.target.value)}
                  className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-150 text-xs focus:border-indigo-500 focus:outline-none transition-colors"
                />
              </div>
              <div>
                <label className="block text-[10px] font-semibold text-zinc-400 mb-1">
                  {t('kioskPairingKeyLabel') || 'Pairing Key (1234AB)'}
                </label>
                <input
                  type="text"
                  required
                  placeholder="e.g. 1234AB"
                  value={pairingKey}
                  onChange={(e) => setPairingKey(e.target.value)}
                  className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-150 text-xs focus:border-indigo-500 focus:outline-none transition-colors"
                />
              </div>
              <div className="flex gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setShowPairing(false)}
                  className="flex-1 py-2 text-xs font-bold text-zinc-400 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors cursor-pointer text-center"
                >
                  {t('cancel') || 'Cancel'}
                </button>
                <button
                  type="submit"
                  disabled={pairingSubmitting}
                  className="flex-1 py-2 text-xs font-bold text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg disabled:opacity-50 transition-colors cursor-pointer text-center flex items-center justify-center gap-1.5"
                >
                  {pairingSubmitting && <Loader2 size={12} className="animate-spin" />}
                  {t('kioskPairButton') || 'Connect & Pair'}
                </button>
              </div>
            </form>
          )}

          {msg && (
            <div className="mt-3 p-3 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs rounded-xl font-bold animate-fade-in">
              {msg}
            </div>
          )}
          {errorMsg && (
            <div className="mt-3 p-3 bg-red-500/10 border border-red-500/20 text-red-400 text-xs rounded-xl font-bold animate-fade-in">
              {errorMsg}
            </div>
          )}
        </div>
        {/* Footer Info inside card */}
        <div className="text-center pt-4 border-t border-zinc-800/50 space-y-1">
          <span className="text-[9px] text-zinc-400 font-bold uppercase tracking-wider block">
            {t('kioskBlockedThisId')}
          </span>
          <span className="font-mono text-xs font-black text-indigo-400 bg-indigo-500/5 border border-indigo-500/10 px-3 py-1 rounded-lg inline-block">
            {kioskId || 'UNKNOWN'}
          </span>
        </div>
      </div>
    </div>
  );
}
