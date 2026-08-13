import React, { useState, useEffect, useRef } from 'react';
import { useTranslation } from '../context/TranslationContext';
import type { Language } from '../i18n/translations';

export default function LanguageSelector() {
  const { language, setLanguage } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleSelect = async (lang: Language) => {
    setLanguage(lang);
    setIsOpen(false);
    
    // Save selected language to settings database
    try {
      const getRes = await fetch('/api/settings');
      if (getRes.ok) {
        const settings = await getRes.json();
        await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            ...settings,
            language: lang
          })
        });
      }
    } catch (err) {
      console.error('Failed to sync language selection to settings backend:', err);
    }
  };

  const flags: Record<Language, React.ReactNode> = {
    en: (
      <svg className="w-5 h-3.5 rounded-sm shadow-sm inline-block" viewBox="0 0 60 30" style={{verticalAlign: 'middle'}}>
        <rect width="60" height="30" fill="#012169"/>
        <path d="M0,0 L60,30 M60,0 L0,30" stroke="#fff" strokeWidth="6"/>
        <path d="M0,0 L60,30 M60,0 L0,30" stroke="#C8102E" strokeWidth="4"/>
        <path d="M30,0 V30 M0,15 H60" stroke="#fff" strokeWidth="10"/>
        <path d="M30,0 V30 M0,15 H60" stroke="#C8102E" strokeWidth="6"/>
      </svg>
    ),
    ru: (
      <svg className="w-5 h-3.5 rounded-sm shadow-sm inline-block" viewBox="0 0 9 6" style={{verticalAlign: 'middle'}}>
        <rect width="9" height="2" fill="#fff"/>
        <rect y="2" width="9" height="2" fill="#0039A6"/>
        <rect y="4" width="9" height="2" fill="#D52B1E"/>
      </svg>
    ),
    uk: (
      <svg className="w-5 h-3.5 rounded-sm shadow-sm inline-block" viewBox="0 0 3 2" style={{verticalAlign: 'middle'}}>
        <rect width="3" height="1" fill="#0057B7"/>
        <rect y="1" width="3" height="1" fill="#FFD700"/>
      </svg>
    )
  };

  // Endonyms, deliberately not translated and deliberately not in
  // translations.ts: a language picker names each language in that language,
  // so someone who cannot read the current UI can still find their own.
  const labels: Record<Language, string> = {
    en: 'English',
    ru: 'Русский',
    uk: 'Українська'
  };

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-zinc-950 hover:bg-zinc-900 border border-zinc-800 text-xs text-zinc-300 font-bold transition-all duration-200 cursor-pointer outline-none"
      >
        <span className="text-sm leading-none">{flags[language]}</span>
        <span>{labels[language] || language.toUpperCase()}</span>
        <svg className={`w-3 h-3 text-zinc-500 transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="3">
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {isOpen && (
        <div className="absolute right-0 mt-1.5 w-44 rounded-lg bg-zinc-900 border border-zinc-800 shadow-2xl p-1 z-50 origin-top-right animate-dropdown-in">
          {(['en', 'uk', 'ru'] as Language[]).map((lang) => (
            <button
              type="button"
              key={lang}
              onClick={() => handleSelect(lang)}
              className={`w-full text-left px-3 py-2 text-xs font-semibold rounded-md transition-colors flex items-center justify-between ${
                language === lang
                  ? 'bg-indigo-50 dark:bg-indigo-600/20 text-indigo-800 dark:text-indigo-300 border border-indigo-100 dark:border-indigo-500/20'
                  : 'text-zinc-400 hover:text-zinc-50 hover:bg-zinc-800'
              }`}
            >
              <div className="flex items-center gap-2">
                <span className="text-sm leading-none">{flags[lang]}</span>
                <span>{labels[lang]}</span>
              </div>
              {language === lang && <span className="text-[10px] text-indigo-450">✓</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
