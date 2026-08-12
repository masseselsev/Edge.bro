import React from 'react';
import { AlertTriangle, Loader2, ShieldCheck, ShieldAlert, Thermometer } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';

/**
 * Health badges that sit on top of the DISK DRIVE and CPU hardware cards.
 *
 * Each badge is a summary that has to survive being glanced at, so the colour
 * carries the verdict and the number carries the detail. The two are driven by
 * different things on purpose: SMART has a 0-100 quality score, while the
 * thermal side has no meaningful percentage — a thermal interface is either
 * like its peers or it is not — so it shows a status word and the resistance
 * itself rather than inventing a score to look symmetrical.
 *
 * A badge never shows green for "we have no idea". Absence of data reads as a
 * muted, explicitly unknown state, because on a fleet of a thousand nodes the
 * difference between "healthy" and "never measured" is the whole point.
 */

export type Grade = 'OK' | 'WATCH' | 'REPLACE' | 'UNKNOWN';
export type ThermalStatus = 'OK' | 'WATCH' | 'ALERT' | 'INSUFFICIENT_DATA';

export interface SmartSubScore {
  name: string;
  score: number | null;
  evidence: Record<string, any>;
}

export interface SmartHealth {
  captured_at: string;
  device: string;
  protocol: string | null;
  model: string | null;
  serial: string | null;
  firmware: string | null;
  health_passed: boolean | null;
  temperature_c: number | null;
  power_on_hours: number | null;
  written_bytes: number | null;
  percent_used: number | null;
  score: number | null;
  grade: Grade | null;
  subscores: SmartSubScore[];
  overrides: string[];
  advisories: string[];
  projected_date: string | null;
  days_remaining: number | null;
  percent_used_per_day: number | null;
  bytes_per_day: number | null;
  observation_days: number | null;
  observation_points: number;
  projection_unavailable_reason: string | null;
}

export interface ThermalHealth {
  status: ThermalStatus;
  cohort_status: ThermalStatus | null;
  drift_status: ThermalStatus | null;
  theta_c_per_w: number | null;
  cohort_key: string | null;
  cohort_size: number;
  cohort_median: number | null;
  z_score: number | null;
  excess_ratio: number | null;
  baseline_theta: number | null;
  recent_theta: number | null;
  drift_ratio: number | null;
  reasons: string[];
  windows_fitted: number;
  windows_rejected: number;
  last_rejection: string | null;
}

export interface NodeHealth {
  node_id: number;
  hostname: string;
  last_harvest_at: string | null;
  monitoring_enabled: boolean;
  capabilities: Record<string, any> | null;
  smart: SmartHealth[];
  thermal: ThermalHealth | null;
}

/**
 * Continuous colour from green to red, rather than three fixed buckets.
 *
 * The user asked for a badge that shades from bright green to bright red, and
 * a drive at 79 should not look identical to one at 41 merely because both
 * fall in "amber". Hue runs 140deg (green) to 0deg (red) across the score.
 */
export function scoreColour(score: number | null | undefined): string {
  if (score === null || score === undefined) return 'hsl(220 8% 45%)';
  const clamped = Math.max(0, Math.min(100, score));
  // Weighted so the interesting range is the top half: a drive does not become
  // "half bad" at 50, it becomes alarming well before that.
  const hue = 140 * Math.pow(clamped / 100, 1.8);
  return `hsl(${hue.toFixed(0)} 70% 45%)`;
}

export function scoreTextColour(score: number | null | undefined): string {
  if (score === null || score === undefined) return 'hsl(220 8% 65%)';
  const clamped = Math.max(0, Math.min(100, score));
  const hue = 140 * Math.pow(clamped / 100, 1.8);
  return `hsl(${hue.toFixed(0)} 75% 62%)`;
}

const THERMAL_TONE: Record<ThermalStatus, { border: string; text: string; bg: string }> = {
  OK: { border: 'border-emerald-500/30', text: 'text-emerald-400', bg: 'bg-emerald-500/10' },
  WATCH: { border: 'border-amber-500/30', text: 'text-amber-400', bg: 'bg-amber-500/10' },
  ALERT: { border: 'border-rose-500/30', text: 'text-rose-400', bg: 'bg-rose-500/10' },
  INSUFFICIENT_DATA: { border: 'border-zinc-700/60', text: 'text-zinc-500', bg: 'bg-zinc-800/40' },
};

interface SmartBadgeProps {
  smart?: SmartHealth;
  loading?: boolean;
  onClick?: () => void;
}

export function SmartBadge({ smart, loading, onClick }: SmartBadgeProps) {
  const { t } = useTranslation();

  if (loading) {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md bg-zinc-800/60 border border-zinc-700/60">
        <Loader2 size={10} className="animate-spin text-zinc-500" />
      </span>
    );
  }

  if (!smart || smart.score === null) {
    return (
      <span
        className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md bg-zinc-800/40 border border-zinc-700/60 text-[9px] font-bold uppercase tracking-wide text-zinc-500"
        title={t('healthNeverMeasuredHint')}
      >
        {t('healthSmart')} <span className="text-zinc-600">—</span>
      </span>
    );
  }

  const critical = smart.grade === 'REPLACE';
  return (
    <button
      onClick={onClick}
      title={t('healthSmartHint')}
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md border text-[9px] font-bold uppercase tracking-wide transition-colors hover:brightness-125 cursor-pointer"
      style={{
        borderColor: scoreColour(smart.score),
        backgroundColor: `${scoreColour(smart.score)}1a`,
        color: scoreTextColour(smart.score),
      }}
    >
      {critical && <AlertTriangle size={9} />}
      {t('healthSmart')}
      <span className="tabular-nums">{smart.score}%</span>
    </button>
  );
}

interface ThermalBadgeProps {
  thermal?: ThermalHealth | null;
  loading?: boolean;
  onClick?: () => void;
}

export function ThermalBadge({ thermal, loading, onClick }: ThermalBadgeProps) {
  const { t } = useTranslation();

  if (loading) {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md bg-zinc-800/60 border border-zinc-700/60">
        <Loader2 size={10} className="animate-spin text-zinc-500" />
      </span>
    );
  }

  const status: ThermalStatus = thermal?.status || 'INSUFFICIENT_DATA';
  const tone = THERMAL_TONE[status];
  const unknown = status === 'INSUFFICIENT_DATA';

  // The tooltip carries why, because "insufficient data" on its own invites
  // the assumption that something is broken.
  const hint = unknown
    ? (thermal?.windows_rejected
        ? t('healthThermalNoFitHint', { rejected: thermal.windows_rejected })
        : t('healthNeverMeasuredHint'))
    : (thermal?.reasons?.[0] || t('healthThermalHint'));

  return (
    <button
      onClick={onClick}
      title={hint}
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md border text-[9px] font-bold uppercase tracking-wide transition-colors hover:brightness-125 cursor-pointer ${tone.border} ${tone.bg} ${tone.text}`}
    >
      {status === 'ALERT' ? <ShieldAlert size={9} /> : unknown ? <Thermometer size={9} /> : <ShieldCheck size={9} />}
      {t('healthThermal')}
      {thermal?.theta_c_per_w != null && (
        <span className="tabular-nums">{thermal.theta_c_per_w.toFixed(2)}</span>
      )}
      {unknown && <span className="text-zinc-600">—</span>}
    </button>
  );
}
