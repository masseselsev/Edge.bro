/**
 * Byte formatting, in one place.
 *
 * Seven copies of this had accumulated and they did not agree. Four never
 * clamped the unit index, so anything past the end of their unit list rendered
 * as `undefined` — the archive browser's stopped at GB and printed
 * "1.02 undefined" for a terabyte. Two of the seven treated 0 as a special
 * case and the rest let `Math.log(0)` produce `-Infinity`. Rounding varied
 * between one and two decimals for no reason anyone recorded.
 *
 * Units are binary (1024) but labelled with the decimal SI names, which is
 * what borg, `df` and every other tool the operator is reading alongside this
 * UI also do. Changing the labels to KiB/MiB would be more correct and would
 * make every number on screen disagree with the numbers next to it.
 */

const STEP = 1024;
const SIZE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
const RATE_UNITS = ['B/s', 'KB/s', 'MB/s', 'GB/s', 'TB/s'];

function scale(bytes: number, units: string[], decimals: number): string {
  // Math.log(0) is -Infinity and Math.log of a negative is NaN; both would
  // index the unit array out of bounds.
  if (!Number.isFinite(bytes) || bytes <= 0) return `0 ${units[0]}`;
  const index = Math.min(
    Math.floor(Math.log(bytes) / Math.log(STEP)),
    units.length - 1,
  );
  const value = parseFloat((bytes / Math.pow(STEP, index)).toFixed(decimals));
  return `${value} ${units[index]}`;
}

/** A size, e.g. "4.72 GB". Null and undefined render as an em dash. */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return '—';
  return scale(bytes, SIZE_UNITS, 2);
}

/** A transfer rate, e.g. "1.4 MB/s". One decimal: it moves too fast to read two. */
export function formatBytesPerSecond(bytesPerSecond: number | null | undefined): string {
  if (bytesPerSecond === null || bytesPerSecond === undefined) return '—';
  return scale(bytesPerSecond, RATE_UNITS, 1);
}
