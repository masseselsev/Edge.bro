import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Info, Loader2, RefreshCw, X } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';
import { scoreTextColour } from './NodeHealthBadges';
import type { NodeHealth, SmartHealth, ThermalHealth } from './NodeHealthBadges';
import SmartReportView from './SmartReportView';
import { parseServerDate } from './dateUtils';

/**
 * The detail view behind a health badge: the full statistics of the latest
 * reading, and a graph of every reading before it.
 *
 * Two rules shape this file.
 *
 * A number is never shown without what produced it. The wear projection is a
 * date, which is the only form an operator can act on, but a date with no
 * arithmetic attached is as opaque as the percentage it replaced — so the rate,
 * the span it was measured over and the number of readings sit next to it, and
 * when no projection is possible the reason appears in its place.
 *
 * Absence is stated, never implied. A node with no thermal fits says the load
 * never varied enough; it does not render an empty chart and leave the reader
 * to guess whether that means healthy, broken or unmeasured.
 */

type Tab = 'smart' | 'thermal' | 'telemetry';

interface SmartHistoryPoint {
  captured_at: string;
  device: string;
  score: number | null;
  temperature_c: number | null;
  percent_used: number | null;
  power_on_hours: number | null;
  written_bytes: number | null;
}

interface ThermalHistoryPoint {
  window_start: string;
  rejection: string;
  theta_c_per_w: number | null;
  theta_normalised: number | null;
  tau_seconds: number | null;
  t_ambient_c: number | null;
  excitation: number | null;
  mean_temp_c: number | null;
}

interface TelemetryPoint {
  bucket_start: string;
  power_w_mean: number | null;
  power_w_max: number | null;
  cpu_temp_c_mean: number | null;
  cpu_temp_c_max: number | null;
  board_temp_c_mean: number | null;
  ssd_temp_c_mean: number | null;
  cpu_util_mean: number | null;
  io_service_ms_mean: number | null;
  throttled: boolean;
}

interface SeriesSpec {
  key: string;
  label: string;
  colour: string;
  unit: string;
}

const SMART_SERIES: SeriesSpec[] = [
  { key: 'score', label: 'healthSeriesScore', colour: '#34d399', unit: '%' },
  { key: 'temperature_c', label: 'healthSeriesTemp', colour: '#fbbf24', unit: '°C' },
  { key: 'percent_used', label: 'healthSeriesWear', colour: '#f87171', unit: '%' },
  { key: 'power_on_hours', label: 'healthSeriesPowerOn', colour: '#818cf8', unit: 'h' },
];

const THERMAL_SERIES: SeriesSpec[] = [
  { key: 'theta_c_per_w', label: 'healthSeriesTheta', colour: '#f472b6', unit: '°C/W' },
  { key: 'theta_normalised', label: 'healthSeriesThetaNorm', colour: '#c084fc', unit: '°C/W' },
  { key: 't_ambient_c', label: 'healthSeriesAmbient', colour: '#60a5fa', unit: '°C' },
  { key: 'tau_seconds', label: 'healthSeriesTau', colour: '#94a3b8', unit: 's' },
  { key: 'excitation', label: 'healthSeriesExcitation', colour: '#fb923c', unit: '' },
];

const TELEMETRY_SERIES: SeriesSpec[] = [
  { key: 'cpu_temp_c_mean', label: 'healthSeriesCpuTemp', colour: '#fbbf24', unit: '°C' },
  { key: 'cpu_temp_c_max', label: 'healthSeriesCpuTempMax', colour: '#f87171', unit: '°C' },
  { key: 'power_w_mean', label: 'healthSeriesPower', colour: '#34d399', unit: 'W' },
  { key: 'board_temp_c_mean', label: 'healthSeriesBoardTemp', colour: '#60a5fa', unit: '°C' },
  { key: 'ssd_temp_c_mean', label: 'healthSeriesSsdTemp', colour: '#c084fc', unit: '°C' },
  { key: 'cpu_util_mean', label: 'healthSeriesUtil', colour: '#818cf8', unit: '' },
];

const DEPTH_OPTIONS = [7, 30, 90, 365];

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  const i = bytes > 0 ? Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1) : 0;
  return `${parseFloat((bytes / Math.pow(1024, i)).toFixed(2))} ${units[i]}`;
}

function formatDate(value: string | null | undefined): string {
  const parsed = parseServerDate(value);
  return parsed ? parsed.toLocaleDateString() : '—';
}

/**
 * A dependency-free line chart.
 *
 * Hand-drawn SVG rather than a charting library: the frontend carries none
 * today, and one series of a few hundred points does not justify adding one.
 * Each series is scaled to its own range because they share no unit — plotting
 * °C and °C/W on one axis would flatten whichever has the smaller numbers into
 * a straight line.
 */
function MultiSeriesChart({
  points,
  xKey,
  series,
  selected,
  height = 220,
}: {
  points: any[];
  xKey: string;
  series: SeriesSpec[];
  selected: string[];
  height?: number;
}) {
  const { t } = useTranslation();
  const [hover, setHover] = useState<number | null>(null);

  const active = series.filter(s => selected.includes(s.key));
  const width = 900;
  const pad = { top: 14, right: 14, bottom: 26, left: 14 };

  const times = points.map(p => parseServerDate(p[xKey])?.getTime() ?? NaN).filter(t => !Number.isNaN(t));
  const tMin = Math.min(...times);
  const tMax = Math.max(...times);
  const span = tMax - tMin || 1;

  const scaled = useMemo(() => active.map(spec => {
    const values = points.map(p => (typeof p[spec.key] === 'number' ? p[spec.key] : null));
    const present = values.filter((v): v is number => v !== null);
    if (!present.length) return { spec, path: '', min: null, max: null, values };
    const min = Math.min(...present);
    const max = Math.max(...present);
    // A flat series would divide by zero; give it a band so it draws mid-height.
    const range = max - min || Math.abs(max) || 1;

    let path = '';
    let penDown = false;
    points.forEach((p, i) => {
      const value = values[i];
      const time = parseServerDate(p[xKey])?.getTime() ?? NaN;
      if (value === null || Number.isNaN(time)) {
        // A gap in the data is drawn as a gap, not bridged with a straight
        // line that would imply readings nobody took.
        penDown = false;
        return;
      }
      const x = pad.left + ((time - tMin) / span) * (width - pad.left - pad.right);
      const y = pad.top + (1 - (value - min) / range) * (height - pad.top - pad.bottom);
      path += `${penDown ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)} `;
      penDown = true;
    });
    return { spec, path, min, max, values };
  }), [points, active, xKey, tMin, span, height]);

  if (!points.length) {
    return (
      <div className="h-40 flex items-center justify-center text-xs text-zinc-600 italic">
        {t('healthNoDataForRange')}
      </div>
    );
  }

  const hoverPoint = hover !== null ? points[hover] : null;

  return (
    <div className="space-y-2">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        style={{ height }}
        onMouseLeave={() => setHover(null)}
      >
        {[0, 0.25, 0.5, 0.75, 1].map(f => (
          <line
            key={f}
            x1={pad.left}
            x2={width - pad.right}
            y1={pad.top + f * (height - pad.top - pad.bottom)}
            y2={pad.top + f * (height - pad.top - pad.bottom)}
            stroke="currentColor"
            className="text-zinc-800"
            strokeWidth={1}
          />
        ))}

        {scaled.map(({ spec, path }) => (
          <path key={spec.key} d={path} fill="none" stroke={spec.colour} strokeWidth={1.8}
                strokeLinejoin="round" strokeLinecap="round" />
        ))}

        {points.map((p, i) => {
          const time = parseServerDate(p[xKey])?.getTime() ?? NaN;
          if (Number.isNaN(time)) return null;
          const x = pad.left + ((time - tMin) / span) * (width - pad.left - pad.right);
          return (
            <rect
              key={i}
              x={x - 4}
              y={0}
              width={8}
              height={height}
              fill="transparent"
              onMouseEnter={() => setHover(i)}
            />
          );
        })}

        {hoverPoint && (() => {
          const time = parseServerDate(hoverPoint[xKey])?.getTime() ?? NaN;
          const x = pad.left + ((time - tMin) / span) * (width - pad.left - pad.right);
          return <line x1={x} x2={x} y1={pad.top} y2={height - pad.bottom}
                       stroke="currentColor" className="text-zinc-600" strokeWidth={1} />;
        })()}

        <text x={pad.left} y={height - 8} className="fill-zinc-600" style={{ fontSize: 11 }}>
          {new Date(tMin).toLocaleDateString()}
        </text>
        <text x={width - pad.right} y={height - 8} textAnchor="end"
              className="fill-zinc-600" style={{ fontSize: 11 }}>
          {new Date(tMax).toLocaleDateString()}
        </text>
      </svg>

      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[10px]">
        {scaled.map(({ spec, min, max }) => (
          <span key={spec.key} className="inline-flex items-center gap-1.5 text-zinc-400">
            <span className="w-2.5 h-0.5 rounded" style={{ backgroundColor: spec.colour }} />
            {t(spec.label)}
            {min !== null && (
              <span className="text-zinc-600 tabular-nums">
                {min === max ? min : `${min}–${max}`}{spec.unit}
              </span>
            )}
          </span>
        ))}
      </div>

      {hoverPoint && (
        <div className="text-[10px] text-zinc-400 bg-zinc-950/60 border border-zinc-800 rounded px-2 py-1">
          <span className="text-zinc-500">{parseServerDate(hoverPoint[xKey])?.toLocaleString() ?? '—'}</span>
          {active.map(spec => (
            typeof hoverPoint[spec.key] === 'number' && (
              <span key={spec.key} className="ml-3">
                <span style={{ color: spec.colour }}>{t(spec.label)}</span>{' '}
                <span className="tabular-nums text-zinc-200">
                  {hoverPoint[spec.key]}{spec.unit}
                </span>
              </span>
            )
          ))}
        </div>
      )}
    </div>
  );
}

export function Field({ label, value, tone }: { label: string; value: React.ReactNode; tone?: string }) {
  return (
    <div className="min-w-0">
      <p className="text-[9px] uppercase tracking-wider text-zinc-500 truncate">{label}</p>
      <p className={`text-xs font-semibold mt-0.5 ${tone || 'text-zinc-200'}`}>{value}</p>
    </div>
  );
}

interface Props {
  nodeId: number;
  hostname: string;
  initialTab?: Tab;
  onClose: () => void;
}

export default function NodeHealthModal({ nodeId, hostname, initialTab = 'smart', onClose }: Props) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>(initialTab);
  const [health, setHealth] = useState<NodeHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [harvesting, setHarvesting] = useState(false);

  const [days, setDays] = useState(90);
  const [smartSeries, setSmartSeries] = useState<string[]>(['score', 'temperature_c', 'percent_used']);
  const [thermalSeries, setThermalSeries] = useState<string[]>(['theta_c_per_w', 't_ambient_c']);
  const [telemetrySeries, setTelemetrySeries] = useState<string[]>(['cpu_temp_c_mean', 'power_w_mean']);

  const [smartHistory, setSmartHistory] = useState<SmartHistoryPoint[]>([]);
  const [thermalHistory, setThermalHistory] = useState<ThermalHistoryPoint[]>([]);
  const [telemetry, setTelemetry] = useState<TelemetryPoint[]>([]);
  const [rawReport, setRawReport] = useState<any | null>(null);
  const [showRaw, setShowRaw] = useState(false);

  // Preferences are stored per user rather than per browser, so the same
  // choices follow the person to another machine.
  useEffect(() => {
    fetch('/api/monitoring/preferences')
      .then(r => (r.ok ? r.json() : null))
      .then(data => {
        const p = data?.preferences;
        if (!p) return;
        if (Array.isArray(p.smart_graph_series)) setSmartSeries(p.smart_graph_series);
        if (Array.isArray(p.thermal_graph_series)) setThermalSeries(p.thermal_graph_series);
        if (Array.isArray(p.telemetry_graph_series)) setTelemetrySeries(p.telemetry_graph_series);
        if (typeof p.graph_days === 'number') setDays(p.graph_days);
      })
      .catch(() => {});
  }, []);

  const savePreferences = useCallback((patch: Record<string, any>) => {
    fetch('/api/monitoring/preferences', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ preferences: patch }),
    }).catch(() => {});
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [healthRes, smartRes, thermalRes, teleRes] = await Promise.all([
        fetch(`/api/monitoring/nodes/${nodeId}`),
        fetch(`/api/monitoring/nodes/${nodeId}/smart-history?days=${days}`),
        fetch(`/api/monitoring/nodes/${nodeId}/thermal-history?days=${days}`),
        fetch(`/api/monitoring/nodes/${nodeId}/telemetry?days=${Math.min(days, 365)}`),
      ]);
      setHealth(healthRes.ok ? await healthRes.json() : null);
      setSmartHistory(smartRes.ok ? await smartRes.json() : []);
      setThermalHistory(thermalRes.ok ? await thermalRes.json() : []);
      setTelemetry(teleRes.ok ? await teleRes.json() : []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [nodeId, days]);

  useEffect(() => { load(); }, [load]);

  const openRaw = async () => {
    if (rawReport) { setShowRaw(v => !v); return; }
    try {
      const res = await fetch(`/api/monitoring/nodes/${nodeId}/smart-latest`);
      if (res.ok) {
        setRawReport((await res.json()).report);
        setShowRaw(true);
      } else {
        setRawReport({ error: (await res.json()).detail });
        setShowRaw(true);
      }
    } catch (e) { console.error(e); }
  };

  const harvest = async () => {
    setHarvesting(true);
    try {
      await fetch(`/api/monitoring/nodes/${nodeId}/harvest`, { method: 'POST' });
      // The harvest runs on a worker; give it a moment before re-reading.
      setTimeout(() => { load(); setHarvesting(false); }, 4000);
    } catch {
      setHarvesting(false);
    }
  };

  const toggleSeries = (current: string[], key: string, setter: (v: string[]) => void, prefKey: string) => {
    const next = current.includes(key) ? current.filter(k => k !== key) : [...current, key];
    setter(next);
    savePreferences({ [prefKey]: next });
  };

  const smart: SmartHealth | undefined = health?.smart?.[0];
  const thermal: ThermalHealth | null | undefined = health?.thermal;

  const seriesFor = tab === 'smart' ? SMART_SERIES : tab === 'thermal' ? THERMAL_SERIES : TELEMETRY_SERIES;
  const selectedFor = tab === 'smart' ? smartSeries : tab === 'thermal' ? thermalSeries : telemetrySeries;
  const setterFor = tab === 'smart' ? setSmartSeries : tab === 'thermal' ? setThermalSeries : setTelemetrySeries;
  const prefKeyFor = tab === 'smart' ? 'smart_graph_series' : tab === 'thermal' ? 'thermal_graph_series' : 'telemetry_graph_series';
  const pointsFor = tab === 'smart' ? smartHistory : tab === 'thermal' ? thermalHistory : telemetry;
  const xKeyFor = tab === 'smart' ? 'captured_at' : tab === 'thermal' ? 'window_start' : 'bucket_start';

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4 overflow-y-auto bg-black/70 backdrop-blur-sm animate-fade-in">
      <div className="bg-zinc-900 border border-zinc-800 rounded-2xl w-full max-w-5xl max-h-[92dvh] my-auto flex flex-col shadow-2xl animate-modal-in">
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-zinc-800">
          <div>
            <h3 className="text-sm font-bold text-zinc-50">{t('healthTitle')}</h3>
            <p className="text-[11px] text-zinc-500">
              {hostname}
              {health?.last_harvest_at && (
                <span className="ml-2">
                  {t('healthLastHarvest')} {parseServerDate(health.last_harvest_at)?.toLocaleString() ?? '—'}
                </span>
              )}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={harvest}
              disabled={harvesting}
              className="inline-flex items-center gap-1.5 px-2.5 py-1 text-[11px] rounded-lg border border-indigo-500/25 text-indigo-400 hover:bg-indigo-500/10 disabled:opacity-40"
            >
              {harvesting ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
              {t('healthHarvestNow')}
            </button>
            <button onClick={onClose} className="p-1.5 text-zinc-500 hover:text-zinc-200 rounded">
              <X size={18} />
            </button>
          </div>
        </div>

        <div className="flex items-center gap-1 px-5 pt-3">
          {(['smart', 'thermal', 'telemetry'] as Tab[]).map(name => (
            <button
              key={name}
              onClick={() => setTab(name)}
              className={`px-3 py-1.5 text-xs font-semibold rounded-lg transition-colors ${
                tab === name ? 'bg-indigo-600 text-white' : 'text-zinc-400 hover:text-zinc-100'
              }`}
            >
              {t(`healthTab_${name}`)}
            </button>
          ))}
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {loading ? (
            <div className="h-48 flex items-center justify-center">
              <Loader2 size={22} className="animate-spin text-zinc-600" />
            </div>
          ) : (
            <>
              {tab === 'smart' && (
                smart ? (
                  <div className="space-y-4">
                    <div className="p-3 bg-zinc-950/50 border border-zinc-800/80 rounded-xl space-y-3">
                      <div className="flex items-baseline gap-3">
                        <span className="text-2xl font-bold tabular-nums"
                              style={{ color: scoreTextColour(smart.score) }}>
                          {smart.score ?? '—'}%
                        </span>
                        <span className="text-xs text-zinc-400">
                          {smart.model} · {smart.device} · {smart.protocol}
                        </span>
                      </div>

                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                        <Field label={t('healthTemp')} value={smart.temperature_c != null ? `${smart.temperature_c} °C` : '—'} />
                        <Field label={t('healthWear')} value={smart.percent_used != null ? `${smart.percent_used}%` : '—'} />
                        <Field label={t('healthPowerOn')} value={smart.power_on_hours != null ? `${smart.power_on_hours} h` : '—'} />
                        <Field label={t('healthWritten')} value={formatBytes(smart.written_bytes)} />
                      </div>

                      {/* The projection with its derivation attached. A date on
                          its own is as opaque as the percentage it replaced. */}
                      <div className="pt-2 border-t border-zinc-800/70">
                        {smart.projected_date ? (
                          <>
                            <p className="text-xs text-zinc-200">
                              {t('healthWearOutBy')}{' '}
                              <span className="font-bold text-amber-400">
                                {formatDate(smart.projected_date)}
                              </span>
                            </p>
                            <p className="text-[10px] text-zinc-500 mt-1">
                              {t('healthProjectionBasis', {
                                used: smart.percent_used ?? 0,
                                rate: (smart.percent_used_per_day ?? 0).toFixed(4),
                                days: smart.observation_days ?? 0,
                                points: smart.observation_points,
                              })}
                              {smart.bytes_per_day != null &&
                                ` · ${formatBytes(smart.bytes_per_day)}${t('healthPerDay')}`}
                            </p>
                            <p className="text-[10px] text-zinc-600 italic mt-1">
                              {t('healthProjectionCaveat')}
                            </p>
                          </>
                        ) : (
                          <p className="text-[11px] text-zinc-500">
                            {t('healthNoProjection')}
                            {smart.projection_unavailable_reason && (
                              <span className="text-zinc-600"> — {smart.projection_unavailable_reason}</span>
                            )}
                          </p>
                        )}
                      </div>
                    </div>

                    {smart.subscores.length > 0 && (
                      <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
                        {smart.subscores.map(sub => (
                          <div key={sub.name}
                               className="p-2 bg-zinc-950/40 border border-zinc-800/60 rounded-lg"
                               title={JSON.stringify(sub.evidence)}>
                            <p className="text-[9px] uppercase tracking-wider text-zinc-500">
                              {t(`healthSub_${sub.name}`)}
                            </p>
                            <p className="text-sm font-bold tabular-nums"
                               style={{ color: scoreTextColour(sub.score) }}>
                              {sub.score === null ? t('healthNotReported') : Math.round(sub.score)}
                            </p>
                          </div>
                        ))}
                      </div>
                    )}

                    {smart.overrides.map(o => (
                      <p key={o} className="text-[11px] text-rose-400 flex items-start gap-1.5">
                        <AlertTriangle size={12} className="mt-0.5 shrink-0" />{o}
                      </p>
                    ))}
                    {smart.advisories.map(a => (
                      <p key={a} className="text-[11px] text-amber-400/90 flex items-start gap-1.5">
                        <Info size={12} className="mt-0.5 shrink-0" />{a}
                      </p>
                    ))}

                    <div>
                      <button onClick={openRaw}
                              className="text-[11px] text-indigo-400 hover:underline">
                        {showRaw ? t('healthHideRaw') : t('healthShowRaw')}
                      </button>
                      {showRaw && rawReport && (
                        <div className="mt-2">
                          <SmartReportView report={rawReport} t={t} />
                        </div>
                      )}
                    </div>
                  </div>
                ) : (
                  <p className="text-xs text-zinc-500 italic">{t('healthNoSmart')}</p>
                )
              )}

              {tab === 'thermal' && (
                <div className="p-3 bg-zinc-950/50 border border-zinc-800/80 rounded-xl space-y-3">
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                    <Field label={t('healthTheta')}
                           value={thermal?.theta_c_per_w != null ? `${thermal.theta_c_per_w} °C/W` : '—'} />
                    <Field label={t('healthCohortMedian')}
                           value={thermal?.cohort_median != null ? `${thermal.cohort_median} °C/W` : '—'} />
                    <Field label={t('healthCohort')}
                           value={`${thermal?.cohort_key || '—'} (${thermal?.cohort_size ?? 0})`} />
                    <Field label={t('healthDrift')}
                           value={thermal?.drift_ratio != null ? `${((thermal.drift_ratio - 1) * 100).toFixed(0)}%` : '—'} />
                  </div>

                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-2 border-t border-zinc-800/70">
                    <Field label={t('healthCohortVerdict')} value={t(`healthStatus_${thermal?.cohort_status || 'INSUFFICIENT_DATA'}`)} />
                    <Field label={t('healthDriftVerdict')} value={t(`healthStatus_${thermal?.drift_status || 'INSUFFICIENT_DATA'}`)} />
                    <Field label={t('healthWindowsFitted')} value={thermal?.windows_fitted ?? 0} />
                    <Field label={t('healthWindowsRejected')}
                           value={`${thermal?.windows_rejected ?? 0}${thermal?.last_rejection ? ` (${thermal.last_rejection})` : ''}`} />
                  </div>

                  {thermal?.reasons?.length ? (
                    thermal.reasons.map(r => (
                      <p key={r} className="text-[11px] text-amber-400 flex items-start gap-1.5">
                        <AlertTriangle size={12} className="mt-0.5 shrink-0" />{r}
                      </p>
                    ))
                  ) : (
                    <p className="text-[10px] text-zinc-600 italic">{t('healthThetaExplainer')}</p>
                  )}

                  {health?.capabilities && health.capabilities.rapl === false && (
                    <p className="text-[11px] text-zinc-500 flex items-start gap-1.5">
                      <Info size={12} className="mt-0.5 shrink-0" />{t('healthNoRapl')}
                    </p>
                  )}
                </div>
              )}

              <div className="p-3 bg-zinc-950/40 border border-zinc-800/70 rounded-xl space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap gap-1.5">
                    {seriesFor.map(spec => (
                      <button
                        key={spec.key}
                        onClick={() => toggleSeries(selectedFor, spec.key, setterFor, prefKeyFor)}
                        className={`px-2 py-0.5 text-[10px] rounded-md border transition-colors ${
                          selectedFor.includes(spec.key)
                            ? 'border-transparent text-zinc-900 font-semibold'
                            : 'border-zinc-700 text-zinc-400 hover:text-zinc-200'
                        }`}
                        style={selectedFor.includes(spec.key) ? { backgroundColor: spec.colour } : undefined}
                      >
                        {t(spec.label)}
                      </button>
                    ))}
                  </div>
                  <div className="inline-flex rounded-lg border border-zinc-800 p-0.5 bg-zinc-950">
                    {DEPTH_OPTIONS.map(d => (
                      <button
                        key={d}
                        onClick={() => { setDays(d); savePreferences({ graph_days: d }); }}
                        className={`px-2 py-0.5 text-[10px] font-semibold rounded-md transition-colors ${
                          days === d ? 'bg-indigo-600 text-white' : 'text-zinc-400 hover:text-zinc-100'
                        }`}
                      >
                        {d}{t('healthDaysShort')}
                      </button>
                    ))}
                  </div>
                </div>

                <MultiSeriesChart points={pointsFor} xKey={xKeyFor}
                                  series={seriesFor} selected={selectedFor} />
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
