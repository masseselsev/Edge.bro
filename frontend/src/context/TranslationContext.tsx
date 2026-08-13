import React, { createContext, useContext, useState, useEffect } from 'react';
import { translations } from '../i18n/translations';
import type { Language } from '../i18n/translations';

interface TranslationContextProps {
  language: Language;
  setLanguage: (lang: Language) => void;
  t: (key: string, variables?: Record<string, any>) => string;
}

const TranslationContext = createContext<TranslationContextProps | undefined>(undefined);

export function TranslationProvider({ children }: { children: React.ReactNode }) {
  const [language, setLanguageState] = useState<Language>('en');

  // Nothing here logs its responses. /api/settings carries the fleet's
  // bootstrap SSH credentials in plaintext, and this runs on every page load,
  // so a debug dump of the response put those passwords in the browser
  // console of anyone who opened devtools.
  const fetchLanguageSetting = async () => {
    try {
      // 1. First detect if kiosk mode is active by fetching version
      const vRes = await fetch('/api/version');
      if (vRes.ok) {
        const vData = await vRes.json();
        if (vData && vData.is_kiosk && vData.language) {
          setLanguageState(vData.language as Language);
          return;
        }
      }

      // 2. Otherwise fetch from normal settings API
      const sRes = await fetch('/api/settings');
      if (sRes.ok) {
        const sData = await sRes.json();
        if (sData && sData.language) {
          setLanguageState(sData.language as Language);
        }
      }
    } catch (err) {
      console.error('Failed to load language setting:', err);
    }
  };

  useEffect(() => {
    fetchLanguageSetting();
  }, []);

  const setLanguage = (lang: Language) => {
    setLanguageState(lang);
  };

  const t = (key: string, variables?: Record<string, any>): string => {
    const langDict = translations[language];
    if (!langDict) return key;
    let translation = langDict[key];
    if (translation === undefined) {
      // Fallback to English translation
      translation = translations['en'][key] || key;
    }
    if (variables) {
      Object.entries(variables).forEach(([k, v]) => {
        translation = translation.replace(new RegExp(`{${k}}`, 'g'), String(v));
      });
    }
    return translation;
  };

  return (
    <TranslationContext.Provider value={{ language, setLanguage, t }}>
      {children}
    </TranslationContext.Provider>
  );
}

export function useTranslation() {
  const context = useContext(TranslationContext);
  if (!context) {
    throw new Error('useTranslation must be used within a TranslationProvider');
  }
  return context;
}
