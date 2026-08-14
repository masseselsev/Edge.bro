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

/**
 * How many bytes restoring or syncing one archive will actually move.
 *
 * `compressed_size` is the answer whenever it is recorded: every chunk the
 * archive references, compressed, which is exactly what a restore streams and
 * what the kiosk's mini-repo ends up holding.
 *
 * `deduplicated_size` is NOT that number and must never be used for it. It is
 * the archive's contribution to the repository, so a second backup of an
 * unchanged node reads as a few hundred KB while still being gigabytes to
 * restore — the UI showed 743 KB against a 1.27 GB download for exactly this
 * reason.
 *
 * Rows written before `compressed_size` existed fall back to the old estimate:
 * whichever is larger of the archive's contribution and 40% of its uncompressed
 * size. That floor is a guess at a typical compression ratio, and it is the
 * only thing recoverable from what those rows stored — the borg output the real
 * figure came from is not kept.
 */
export function downloadSizeBytes(archive: {
  compressed_size?: number | null;
  deduplicated_size: number;
  original_size: number;
}): number {
  if (archive.compressed_size != null) return archive.compressed_size;
  return Math.max(archive.deduplicated_size, Math.round(archive.original_size * 0.4));
}

/** True when the figure above is the recorded one rather than the estimate. */
export function isExactDownloadSize(archive: { compressed_size?: number | null }): boolean {
  return archive.compressed_size != null;
}
