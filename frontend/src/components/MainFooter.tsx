import React from 'react';
import { AlertTriangle } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';

interface MainFooterProps {
  appVersion: string;
  healthWarnings: any[];
  setActiveTab: (tab: any) => void;
}

export default function MainFooter({ appVersion, healthWarnings, setActiveTab }: MainFooterProps) {
  const { t } = useTranslation();

  return (
    <footer className="fixed bottom-0 left-0 right-0 z-40 py-4 px-6 border-t border-zinc-900/60 bg-zinc-950/95 backdrop-blur-md text-center text-xs text-zinc-500 flex flex-wrap items-center justify-center gap-4 animate-fade-in">
      <span>Edge B.R.O.</span>
      <span className="h-4 w-px bg-zinc-900" />
      <span>{appVersion ? (appVersion.startsWith('v') ? appVersion : `v${appVersion}`) : 'v2.1.0'}</span>
      {healthWarnings.length > 0 && (
        <>
          <span className="h-4 w-px bg-zinc-900" />
          <button
            type="button"
            onClick={() => setActiveTab('settings')}
            className="flex items-center gap-1 text-amber-500 font-bold hover:text-amber-400 cursor-pointer animate-pulse transition-all animate-pulse duration-1000"
          >
            <AlertTriangle size={13} />
            <span>{t('warningsCount')} ({healthWarnings.length})</span>
          </button>
        </>
      )}
    </footer>
  );
}
