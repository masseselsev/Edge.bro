import React from 'react';
import { useTranslation } from '../context/TranslationContext';
import type { PendingKiosk } from '../hooks/usePendingKiosks';

/**
 * "A kiosk is asking to connect" — shown across the top until it is dealt with.
 *
 * Only the oldest request is named, with a count for the rest. Somebody is
 * usually waiting on the phone, so this sits above the tab content rather than
 * in a notification list where it can be missed.
 */

export interface PendingKioskBannerProps {
  pending: PendingKiosk[];
  onReview: (kiosk: PendingKiosk) => void;
}

export default function PendingKioskBanner({ pending, onReview }: PendingKioskBannerProps) {
  const { t } = useTranslation();
  const [first] = pending;

  return (
    <div className="bg-zinc-950 border-b border-amber-500/20 py-2.5 px-6 shadow-md transition-all duration-300 ease-in-out animate-fade-in">
      <div className="max-w-7xl mx-auto flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-2 text-xs font-semibold text-zinc-300">
          <span className="flex h-2 w-2 relative">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75"></span>
            <span className="relative inline-flex rounded-full h-2 w-2 bg-amber-500"></span>
          </span>
          <span>
            {t('pendingConnectionBanner')
              .replace('{name}', first.name || t('unnamedKiosk') || 'Unnamed')
              .replace('{phone}', first.phone || '')}
            {pending.length > 1 ? ` (+${pending.length - 1})` : ''}
          </span>
        </div>
        <button
          onClick={() => onReview(first)}
          className="px-3 py-1 bg-amber-500 hover:bg-amber-400 text-zinc-950 rounded text-[11px] font-bold transition-all duration-200 cursor-pointer shadow-[0_0_12px_rgba(245,158,11,0.2)] hover:shadow-[0_0_16px_rgba(245,158,11,0.4)]"
        >
          {t('reviewRequest') || 'Review Request'}
        </button>
      </div>
    </div>
  );
}
