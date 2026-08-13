/**
 * Parse a timestamp the way the backend actually means it.
 *
 * The API returns naive UTC — no offset, no trailing Z. JavaScript reads a
 * bare `2026-08-13T10:00:00` as *local* time, so passing one straight to
 * `new Date()` silently shifts it by the viewer's offset. Every timestamp
 * coming from the server must go through here rather than `new Date(x)`,
 * including ones only used for arithmetic or chart axes, where the error is
 * invisible instead of merely wrong.
 */
export function parseServerDate(dateStr: string | null | undefined): Date | null {
  if (!dateStr) return null;
  let normalized = dateStr;
  if (
    !dateStr.endsWith('Z') &&
    !dateStr.includes('+') &&
    !/[-+]\d{2}:?\d{2}$/.test(dateStr)
  ) {
    normalized = dateStr.replace(' ', 'T') + 'Z';
  }
  const date = new Date(normalized);
  return isNaN(date.getTime()) ? null : date;
}

export function formatDate(dateStr: string | null | undefined, timezone?: string): string {
  const date = parseServerDate(dateStr);
  if (!date) return '';

  const options: Intl.DateTimeFormatOptions = {
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: 'numeric',
    second: 'numeric',
  };

  if (timezone && timezone !== 'Browser Local') {
    try {
      options.timeZone = timezone;
    } catch (e) {
      console.warn(`Invalid timezone specified: ${timezone}`, e);
    }
  }

  return new Intl.DateTimeFormat(undefined, options).format(date);
}

/**
 * A date from an epoch-milliseconds value that is already an absolute instant.
 *
 * Distinct from `formatDate`, which takes the naive-UTC strings the API
 * returns and has to attach the timezone itself. Here the ambiguity is already
 * resolved — chart axes work in milliseconds derived from `parseServerDate` —
 * so the only job left is rendering it in the viewer's locale. Kept as a named
 * function so `new Date(x).toLocaleDateString()` never has to appear at a call
 * site, where it is almost always the naive-UTC bug instead.
 */
export function formatEpochDate(epochMs: number): string {
  if (!Number.isFinite(epochMs)) return '';
  return new Date(epochMs).toLocaleDateString();
}
