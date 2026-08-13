import en from './en';

/**
 * UI strings, with only the language in use downloaded.
 *
 * All three dictionaries used to live in one 3000-line module that every
 * bundle carried — about 178KB of source shipped to every user so that two
 * thirds of it could sit unread. They are one file each now, and `loadLanguage`
 * is a dynamic import, so Russian and Ukrainian become separate chunks fetched
 * only when someone selects them.
 *
 * **English is deliberately not lazy.** `t()` falls back to the English string
 * for any key the active language is missing, so English has to be present
 * before the first render or a partially translated key renders as its own
 * name. It is the one dictionary that is always worth its bytes.
 */

export type Language = 'en' | 'ru' | 'uk';

export type Dictionary = Record<string, string>;

export const LANGUAGES: Language[] = ['en', 'ru', 'uk'];

/** Always available, and the fallback for every missing key. */
export const englishDictionary: Dictionary = en;

/**
 * Fetch a language's dictionary, as its own chunk.
 *
 * Returns English immediately rather than importing it again — bundlers treat
 * a statically imported module and a dynamically imported one as the same
 * chunk, but going through the promise would still add a microtask to the
 * first paint for no gain.
 *
 * A failed import resolves to English rather than rejecting. The network being
 * unavailable is the normal state on a kiosk, and an untranslated console is a
 * far better outcome than a blank one.
 */
export async function loadLanguage(language: Language): Promise<Dictionary> {
  if (language === 'en') return en;
  try {
    const module = language === 'ru' ? await import('./ru') : await import('./uk');
    return module.default;
  } catch (error) {
    console.error(`Failed to load the ${language} dictionary; falling back to English.`, error);
    return en;
  }
}
