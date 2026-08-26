// Upload rate limits are stored and sent to the API as whole KiB/s (borg's
// own unit), but that makes people compare against an ISP's Mbit/s rating by
// doing the KiB->Mbit->bits arithmetic in their head. These convert so the
// UI can show and accept Mbit/s while the wire format stays KiB/s.
const KIB_PER_MBIT = 1_000_000 / (1024 * 8);

export function kibToMbit(kib: number): number {
  return kib / KIB_PER_MBIT;
}

export function mbitToKib(mbit: number): number {
  return Math.round(mbit * KIB_PER_MBIT);
}

// Accepts a comma as the decimal separator (e.g. "2,5") alongside a dot.
// Returns null for blank input (meaning "unlimited") or anything that isn't
// a non-negative number.
export function parseMbitInput(raw: string): number | null {
  const trimmed = raw.trim();
  if (trimmed === '') return null;
  const value = Number(trimmed.replace(',', '.'));
  if (!Number.isFinite(value) || value < 0) return null;
  return value;
}

// Mbit/s values read comfortably with at most two decimals; trailing zeros
// are trimmed so a whole number like 2 doesn't render as "2.00".
export function formatMbit(mbit: number): string {
  return String(Math.round(mbit * 100) / 100);
}

// What a running backup is actually achieving, against the limit it is
// allowed — "42.3 / 50 Mbit/s", or just "42.3 Mbit/s" when uncapped. Returns
// null for the first seconds of a transfer, before borg's rolling window can
// state a rate: the caller shows its plain label rather than a zero.
export function formatLiveSpeed(node: {
  current_speed_mbps?: number | null;
  current_speed_limit_mbps?: number | null;
}): string | null {
  if (node.current_speed_mbps == null) return null;
  const current = formatMbit(node.current_speed_mbps);
  if (node.current_speed_limit_mbps == null) return `${current} Mbit/s`;
  return `${current} / ${formatMbit(node.current_speed_limit_mbps)} Mbit/s`;
}

// os_version is stored verbatim from /etc/os-release (NAME + VERSION_ID),
// e.g. "Debian GNU/Linux 10" or "Ubuntu 22.04". The "GNU/Linux" is
// redundant in a table cell that already has its own OS/ARCH column header
// — strip it. Ubuntu has no such redundancy and passes through unchanged.
export function formatOsVersion(osVersion: string | null | undefined): string {
  if (!osVersion) return '';
  return osVersion.replace('GNU/Linux ', '');
}
