import React from 'react';
import { Loader2 } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';

/**
 * Covers the app until version, settings and network status have all answered.
 *
 * It is an overlay rather than an early return because the tree underneath is
 * mounting and fetching the whole time — swapping it in for the real layout
 * would restart every one of those requests when it went away.
 */
export default function BootOverlay() {
  const { t } = useTranslation();

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-zinc-950/95 backdrop-blur-xl transition-opacity duration-500">
      <div className="flex flex-col items-center gap-5 animate-fade-in">
        <div className="relative p-4 bg-indigo-600/15 border border-indigo-500/30 rounded-2xl shadow-2xl">
          <svg className="w-10 h-10 text-indigo-400 filter drop-shadow-[0_0_8px_rgba(99,102,241,0.6)] animate-pulse" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
          </svg>
          <span className="absolute top-2.5 right-2.5 w-2 h-2 bg-emerald-500 rounded-full animate-ping"></span>
          <span className="absolute top-2.5 right-2.5 w-2 h-2 bg-emerald-500 rounded-full"></span>
        </div>
        <div className="text-center space-y-2">
          <h2 className="text-lg font-bold text-zinc-100 tracking-tight">
            <span className="bg-indigo-500/10 text-indigo-300 border border-indigo-500/20 px-2.5 py-1 rounded font-mono font-bold text-sm uppercase tracking-wider">Edge-B.R.O.</span>
          </h2>
          <div className="flex items-center justify-center gap-2 text-zinc-400 text-xs font-semibold">
            <Loader2 size={14} className="animate-spin text-indigo-400" />
            <span>{t('loadingInitializing')}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
