import React from 'react';
import { AlertCircle, ArrowRight } from 'lucide-react';
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

  if (!first) return null;

  const contact = (first.contact || first.phone || '').trim();
  const name = (first.name || t('unnamedKiosk') || 'Unnamed').trim();

  let message = t('pendingConnectionBanner') || 'Kiosk connection request from {name} ({contact})';
  message = message.replace(/^⚠️\s*/, '').replace('{name}', name);
  if (contact) {
    message = message.replace('{contact}', contact).replace('{phone}', contact);
  } else {
    message = message
      .replace('({contact})', '')
      .replace('({phone})', '')
      .replace('{contact}', '')
      .replace('{phone}', '')
      .trim();
  }

  return (
    <div className="max-w-7xl w-full mx-auto px-6 pt-4">
      <div className="pending-kiosk-alert border rounded-2xl p-3 sm:px-4 sm:py-2.5 flex items-center justify-between gap-4 flex-wrap shadow-sm transition-all duration-300 animate-fade-in">
        <div className="flex items-center gap-3 text-xs font-semibold">
          <span className="flex h-2.5 w-2.5 relative shrink-0">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75"></span>
            <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-amber-500"></span>
          </span>
          <span className="alert-text font-medium leading-snug">
            {message}
            {pending.length > 1 && (
              <span className="alert-badge ml-2 px-2 py-0.5 rounded-full border text-[11px] font-bold">
                +{pending.length - 1}
              </span>
            )}
          </span>
        </div>
        <button
          onClick={() => onReview(first)}
          className="alert-btn inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-xl text-xs font-bold transition-all duration-200 cursor-pointer shadow-sm hover:shadow active:scale-95 shrink-0"
        >
          <span>{t('reviewRequest') || 'Review Request'}</span>
          <ArrowRight size={13} />
        </button>
      </div>
    </div>
  );
}
