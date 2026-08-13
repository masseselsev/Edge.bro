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
