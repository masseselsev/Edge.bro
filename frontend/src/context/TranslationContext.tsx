import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { englishDictionary, loadLanguage, type Dictionary, type Language } from '../i18n';

interface TranslationContextProps {
  language: Language;
  setLanguage: (lang: Language) => void;
  t: (key: string, variables?: Record<string, any>) => string;
}

const TranslationContext = createContext<TranslationContextProps | undefined>(undefined);

export function TranslationProvider({ children }: { children: React.ReactNode }) {
  const [language, setLanguageState] = useState<Language>('en');
  const [dictionary, setDictionary] = useState<Dictionary>(englishDictionary);

  // Nothing here logs its responses. /api/settings carries the fleet's
  // bootstrap SSH credentials in plaintext, and this runs on every page load,
  // so a debug dump of the response put those passwords in the browser
  // console of anyone who opened devtools.
  useEffect(() => {
    const fetchLanguageSetting = async () => {
      try {
        // Kiosk mode carries its language in the image's config, so it is
        // known from /api/version and needs no authenticated call.
        const vRes = await fetch('/api/version');
        if (vRes.ok) {
          const vData = await vRes.json();
          if (vData && vData.is_kiosk && vData.language) {
            setLanguageState(vData.language as Language);
            return;
          }
        }

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
    fetchLanguageSetting();
  }, []);

  // Each non-English dictionary is a separate chunk, so switching language is
  // a network fetch. Until it lands the previous dictionary stays in place and
  // `t` keeps returning readable text rather than blanking the UI.
  useEffect(() => {
    let current = true;
    loadLanguage(language).then(loaded => {
      if (current) setDictionary(loaded);
    });
    return () => { current = false; };
  }, [language]);

  const setLanguage = useCallback((lang: Language) => setLanguageState(lang), []);

  const t = useCallback((key: string, variables?: Record<string, any>): string => {
    // English is the fallback for any key the active language has not
    // translated yet, which is why it is never lazy-loaded.
    let translation = dictionary[key] ?? englishDictionary[key] ?? key;
    if (variables) {
      Object.entries(variables).forEach(([k, v]) => {
        translation = translation.replace(new RegExp(`{${k}}`, 'g'), String(v));
      });
    }
    return translation;
  }, [dictionary]);

  // Memoised so that consumers depending on the context value — and the many
  // components that put `t` in a useMemo dependency list — do not re-render on
  // every provider render.
  const value = useMemo(() => ({ language, setLanguage, t }), [language, setLanguage, t]);

  return (
    <TranslationContext.Provider value={value}>
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
