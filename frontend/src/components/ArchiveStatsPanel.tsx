import React, { useCallback, useEffect, useState } from 'react';
import {
  Activity, AlertTriangle, CheckCircle2, Clock, Database, Gauge,
  HardDrive, Loader2, TrendingDown, TrendingUp
} from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';

/**
 * The Archives page header and the analysis under it.
 *
 * Split out of HistoryTab because that file was already long, and because the
 * two do genuinely different jobs: the table lists archives, this explains what
 * the fleet is doing. The numbers come from /api/stats (cheap, always shown)
 * and /api/stats/insights (a windowed analysis, fetched alongside it).
 *
 * Nothing here fills a gap with a zero. A metric the backend could not compute
 * arrives as null and is rendered as a dash, because "0 Mbit/s" and "no
 * measurement" mean very different things to whoever is reading the page.
 */

interface GlobalStats {
  total_nodes: number;
  nodes_with_archives: number;
  total_archives: number;
  successful_archives: number;
  failed_archives: number;
  success_rate: number | null;
  total_original_size_bytes: number;
  total_deduplicated_size_bytes: number;
  saved_space_bytes: number;
  deduplication_ratio: number | null;
  repo_size_bytes: number | null;
  disk_total_bytes: number | null;
  disk_used_bytes: number | null;
  disk_free_bytes: number | null;
}

interface NodeReliability {
  node_id: number;
  hostname: string;
  group_name: string | null;
  last_success_at: string | null;
  days_since_success: number | null;
  expected_interval_days: number;
  consecutive_failures: number;
  last_error_category: string | null;
  is_stale: boolean;
}

interface NodeSpeed {
  node_id: number;
  hostname: string;
  runs: number;
  median_mbps: number | null;
  max_mbps: number | null;
  limit_source: string | null;
  limit_mbps: number | null;
  limit_binding: boolean | null;
}

interface NodeDuration {
  node_id: number;
  hostname: string;
  median_seconds: number | null;
  max_seconds: number | null;
  group_name: string | null;
  window_minutes: number | null;
  window_usage: number | null;
  at_risk: boolean;
}

interface NodeConsumption {
  node_id: number;
  hostname: string;
  bytes: number;
  share: number | null;
  archives: number;
}

interface Insights {
  window_days: number;
  reliability: {
    total_runs: number;
    successful_runs: number;
    failed_runs: number;
    success_rate: number | null;
    nodes_total: number;
    nodes_never_succeeded: number;
    nodes_stale: number;
    stale_nodes: NodeReliability[];
    failing_nodes: NodeReliability[];
    top_failures: { category: string; count: number }[];
  };
  speed: {
    measured_runs: number;
    median_mbps: number | null;
    p10_mbps: number | null;
    p90_mbps: number | null;
    slowest_nodes: NodeSpeed[];
    capped_nodes: number;
  };
  duration: {
    measured_runs: number;
    median_seconds: number | null;
    p90_seconds: number | null;
    nodes_at_risk: number;
    longest_nodes: NodeDuration[];
  };
  capacity: {
    repo_size_bytes: number | null;
    disk_total_bytes: number | null;
    disk_free_bytes: number | null;
    daily_inflow_bytes: number | null;
    days_until_full: number | null;
    projected_full_date: string | null;
    top_consumers: NodeConsumption[];
  };
}

interface Props {
  /** Bumped by the parent to force a refetch after a refresh or a purge. */
  reloadSignal?: number;
  onSelectNode?: (nodeId: number) => void;
}

const WINDOW_OPTIONS = [7, 30, 90];

function formatSize(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return '—';
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), sizes.length - 1);
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`;
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

function formatMbps(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${value.toFixed(1)} Mbit/s`;
}

/** Green above 95%, amber down to 80%, red below. Missing data stays neutral. */
function rateTone(rate: number | null): string {
  if (rate === null) return 'text-zinc-400';
  if (rate >= 95) return 'text-emerald-400';
  if (rate >= 80) return 'text-amber-400';
  return 'text-rose-400';
}

export default function ArchiveStatsPanel({ reloadSignal = 0, onSelectNode }: Props) {
  const { t } = useTranslation();
  const [stats, setStats] = useState<GlobalStats | null>(null);
  const [insights, setInsights] = useState<Insights | null>(null);
  const [windowDays, setWindowDays] = useState(30);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [statsRes, insightsRes] = await Promise.all([
        fetch('/api/stats'),
        fetch(`/api/stats/insights?days=${windowDays}`),
      ]);
      setStats(statsRes.ok ? await statsRes.json() : null);
      setInsights(insightsRes.ok ? await insightsRes.json() : null);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [windowDays]);

  useEffect(() => {
    load();
  }, [load, reloadSignal]);

  const nodeLink = (nodeId: number, hostname: string) => (
    <span
      onClick={() => onSelectNode?.(nodeId)}
      className={`font-semibold ${onSelectNode ? 'text-indigo-400 hover:underline cursor-pointer' : 'text-zinc-200'}`}
    >
      {hostname}
    </span>
  );

  return (
    <div className="space-y-3">
      {/* Headline figures. Physical size and the sums are shown side by side
          but never mixed: they measure different things. */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
        <Card
          icon={<HardDrive size={16} />}
          tone="indigo"
          label={t('statsStoredOnDisk')}
          value={formatSize(stats?.repo_size_bytes)}
          note={
            stats?.disk_total_bytes
              ? t('statsDiskFree', {
                  free: formatSize(stats.disk_free_bytes),
                  total: formatSize(stats.disk_total_bytes),
                })
              : t('statsStoredOnDiskSub')
          }
        />
        <Card
          icon={<Database size={16} />}
          tone="emerald"
          label={t('statsSourceData')}
          value={formatSize(stats?.total_original_size_bytes)}
          note={t('statsSourceDataSub')}
        />
        <Card
          icon={<TrendingDown size={16} />}
          tone="purple"
          label={t('statsSpaceSaved')}
          value={formatSize(stats?.saved_space_bytes)}
          note={
            stats?.deduplication_ratio
              ? t('statsDedupRatioShort', { ratio: stats.deduplication_ratio })
              : t('statsNoData')
          }
        />
        <Card
          icon={stats && stats.success_rate !== null && stats.success_rate < 80
            ? <AlertTriangle size={16} />
            : <CheckCircle2 size={16} />}
          tone={stats && stats.success_rate !== null && stats.success_rate < 80 ? 'rose' : 'emerald'}
          label={t('statsSuccessRate')}
          value={stats?.success_rate === null || stats === null ? '—' : `${stats.success_rate}%`}
          valueClass={rateTone(stats?.success_rate ?? null)}
          note={stats ? t('statsArchivesSummary', {
            ok: stats.successful_archives,
            total: stats.total_archives,
          }) : ''}
        />
      </div>

      <div className="p-4 bg-zinc-900 border border-zinc-800 rounded-2xl space-y-4">
        <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3">
          <div>
            <h3 className="text-sm font-bold text-zinc-50 flex items-center gap-2">
              <Activity size={15} className="text-indigo-400" />
              {t('statsInsightsTitle')}
            </h3>
            <p className="text-[11px] text-zinc-500 mt-0.5">{t('statsInsightsSub')}</p>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[10px] uppercase tracking-wider text-zinc-500 font-semibold">
              {t('statsWindowLabel')}
            </span>
            <div className="inline-flex rounded-lg border border-zinc-800 p-0.5 bg-zinc-950">
              {WINDOW_OPTIONS.map(days => (
                <button
                  key={days}
                  onClick={() => setWindowDays(days)}
                  className={`px-2.5 py-1 text-[11px] font-semibold rounded-md transition-colors ${
                    windowDays === days ? 'bg-indigo-600 text-white' : 'text-zinc-400 hover:text-zinc-50'
                  }`}
                >
                  {t(`statsWindow${days}`)}
                </button>
              ))}
            </div>
            {loading && <Loader2 size={14} className="animate-spin text-zinc-500" />}
          </div>
        </div>

        {insights && (
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
            {/* --- Reliability --- */}
            <Panel icon={<CheckCircle2 size={14} />} title={t('statsReliability')}>
              <Figures>
                <Figure label={t('statsRunsInWindow')} value={String(insights.reliability.total_runs)} />
                <Figure
                  label={t('statsSuccessRate')}
                  value={insights.reliability.success_rate === null ? '—' : `${insights.reliability.success_rate}%`}
                  tone={rateTone(insights.reliability.success_rate)}
                />
                <Figure
                  label={t('statsStaleNodes')}
                  value={String(insights.reliability.nodes_stale)}
                  tone={insights.reliability.nodes_stale > 0 ? 'text-amber-400' : 'text-zinc-200'}
                />
                <Figure
                  label={t('statsNeverSucceeded')}
                  value={String(insights.reliability.nodes_never_succeeded)}
                  tone={insights.reliability.nodes_never_succeeded > 0 ? 'text-rose-400' : 'text-zinc-200'}
                />
              </Figures>

              {insights.reliability.stale_nodes.length > 0 ? (
                <List title={t('statsStaleNodes')}>
                  {insights.reliability.stale_nodes.map(n => (
                    <Row key={n.node_id} left={nodeLink(n.node_id, n.hostname)}>
                      <span className="text-amber-400 font-semibold">
                        {n.days_since_success === null
                          ? t('statsNeverLabel')
                          : t('statsDaysAgo', { days: n.days_since_success })}
                      </span>
                      <span className="text-zinc-600 ml-1.5">
                        ({t('statsExpectedEvery', { days: n.expected_interval_days })})
                      </span>
                    </Row>
                  ))}
                </List>
              ) : (
                <Empty>{t('statsAllOnSchedule')}</Empty>
              )}

              {insights.reliability.failing_nodes.length > 0 && (
                <List title={t('statsFailingNodes')}>
                  {insights.reliability.failing_nodes.map(n => (
                    <Row key={n.node_id} left={nodeLink(n.node_id, n.hostname)}>
                      <span className="text-rose-400 font-semibold">
                        {t('statsFailuresInARow', { count: n.consecutive_failures })}
                      </span>
                      {n.last_error_category && (
                        <span className="text-zinc-500 ml-1.5">
                          {t(`fail${n.last_error_category}`)}
                        </span>
                      )}
                    </Row>
                  ))}
                </List>
              )}

              {insights.reliability.top_failures.length > 0 ? (
                <List title={t('statsTopFailures')}>
                  {insights.reliability.top_failures.map(f => (
                    <Row key={f.category} left={<span className="text-zinc-300">{t(`fail${f.category}`)}</span>}>
                      <span className="text-zinc-400 font-semibold">{f.count}</span>
                    </Row>
                  ))}
                </List>
              ) : (
                <Empty>{t('statsNoFailures')}</Empty>
              )}
            </Panel>

            {/* --- Throughput --- */}
            <Panel icon={<Gauge size={14} />} title={t('statsSpeed')}>
              {insights.speed.measured_runs === 0 ? (
                <Empty>{t('statsNoSpeedData')}</Empty>
              ) : (
                <>
                  <Figures>
                    <Figure label={t('statsMedianSpeed')} value={formatMbps(insights.speed.median_mbps)} />
                    <Figure
                      label={t('statsSpeedRange')}
                      value={`${insights.speed.p10_mbps?.toFixed(1) ?? '—'} – ${insights.speed.p90_mbps?.toFixed(1) ?? '—'}`}
                    />
                    <Figure label={t('statsRunsInWindow')} value={String(insights.speed.measured_runs)} />
                    <Figure
                      label={t('statsRateLimited')}
                      value={String(insights.speed.capped_nodes)}
                      tone={insights.speed.capped_nodes > 0 ? 'text-amber-400' : 'text-zinc-200'}
                    />
                  </Figures>

                  <List title={t('statsSlowestNodes')}>
                    {insights.speed.slowest_nodes.map(n => (
                      <Row key={n.node_id} left={nodeLink(n.node_id, n.hostname)}>
                        <span className="text-zinc-200 font-semibold">{formatMbps(n.median_mbps)}</span>
                        {n.limit_source && (
                          <span
                            className={`ml-1.5 ${n.limit_binding ? 'text-amber-400' : 'text-zinc-600'}`}
                            title={n.limit_binding ? t('statsLimitBinding') : t('statsLimitNotBinding')}
                          >
                            {n.limit_source === 'node' ? t('statsLimitFromNode') : t('statsLimitFromGroup')}
                            {' '}{formatMbps(n.limit_mbps)}
                          </span>
                        )}
                      </Row>
                    ))}
                  </List>
                </>
              )}
            </Panel>

            {/* --- Duration against the group window --- */}
            <Panel icon={<Clock size={14} />} title={t('statsDuration')}>
              {insights.duration.measured_runs === 0 ? (
                <Empty>{t('statsNoDurationData')}</Empty>
              ) : (
                <>
                  <Figures>
                    <Figure label={t('statsMedianDuration')} value={formatDuration(insights.duration.median_seconds)} />
                    <Figure label={t('statsP90Duration')} value={formatDuration(insights.duration.p90_seconds)} />
                    <Figure label={t('statsRunsInWindow')} value={String(insights.duration.measured_runs)} />
                    <Figure
                      label={t('statsAtRisk')}
                      value={String(insights.duration.nodes_at_risk)}
                      tone={insights.duration.nodes_at_risk > 0 ? 'text-amber-400' : 'text-zinc-200'}
                    />
                  </Figures>

                  <List title={t('statsLongestNodes')}>
                    {insights.duration.longest_nodes.map(n => (
                      <Row key={n.node_id} left={nodeLink(n.node_id, n.hostname)}>
                        <span className={n.at_risk ? 'text-amber-400 font-semibold' : 'text-zinc-200 font-semibold'}>
                          {formatDuration(n.max_seconds)}
                        </span>
                        <span className="text-zinc-600 ml-1.5">
                          {n.window_usage === null
                            ? t('statsNoWindow')
                            : t('statsWindowUsage', { percent: Math.round(n.window_usage * 100) })}
                        </span>
                      </Row>
                    ))}
                  </List>
                </>
              )}
            </Panel>

            {/* --- Capacity --- */}
            <Panel icon={<TrendingUp size={14} />} title={t('statsCapacity')}>
              <Figures>
                <Figure label={t('statsStoredOnDisk')} value={formatSize(insights.capacity.repo_size_bytes)} />
                <Figure
                  label={t('statsDailyInflow')}
                  value={insights.capacity.daily_inflow_bytes === null
                    ? '—'
                    : `${formatSize(insights.capacity.daily_inflow_bytes)}/d`}
                />
                <Figure
                  label={t('statsRunway')}
                  value={insights.capacity.days_until_full === null
                    ? '—'
                    : t('statsDaysLeft', { days: Math.round(insights.capacity.days_until_full) })}
                  tone={
                    insights.capacity.days_until_full !== null && insights.capacity.days_until_full < 90
                      ? 'text-rose-400'
                      : 'text-zinc-200'
                  }
                />
                <Figure label={t('statsFreeSpace')} value={formatSize(insights.capacity.disk_free_bytes)} />
              </Figures>

              {insights.capacity.daily_inflow_bytes === null ? (
                <Empty>{t('statsNoGrowth')}</Empty>
              ) : (
                <p className="text-[10px] text-zinc-600 italic">{t('statsGrowthUpperBound')}</p>
              )}

              {insights.capacity.top_consumers.length > 0 && (
                <List title={t('statsTopConsumers')}>
                  {insights.capacity.top_consumers.map(c => (
                    <Row key={c.node_id} left={nodeLink(c.node_id, c.hostname)}>
                      <span className="text-zinc-200 font-semibold">{formatSize(c.bytes)}</span>
                      {c.share !== null && (
                        <span className="text-zinc-600 ml-1.5">
                          {t('statsShareOfRepo', { percent: Math.round(c.share * 100) })}
                        </span>
                      )}
                    </Row>
                  ))}
                </List>
              )}
            </Panel>
          </div>
        )}
      </div>
    </div>
  );
}

// --- small presentational pieces --------------------------------------------

const TONES: Record<string, string> = {
  indigo: 'bg-indigo-500/10 text-indigo-400 border-indigo-500/20',
  emerald: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
  purple: 'bg-purple-500/10 text-purple-400 border-purple-500/20',
  rose: 'bg-rose-500/10 text-rose-400 border-rose-500/20',
};

function Card({ icon, tone, label, value, note, valueClass }: {
  icon: React.ReactNode;
  tone: keyof typeof TONES | string;
  label: string;
  value: string;
  note?: string;
  valueClass?: string;
}) {
  return (
    <div className="p-3 bg-zinc-900 border border-zinc-800 rounded-xl flex items-center gap-3">
      <div className={`p-1.5 rounded-lg border ${TONES[tone] || TONES.indigo}`}>{icon}</div>
      <div className="min-w-0">
        <p className="text-[10px] text-zinc-400 font-medium uppercase tracking-wider">{label}</p>
        <h4 className={`text-base font-bold mt-0.5 ${valueClass || 'text-zinc-50'}`}>{value}</h4>
        {note && <p className="text-[9px] text-zinc-500 truncate" title={note}>{note}</p>}
      </div>
    </div>
  );
}

function Panel({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <div className="p-3.5 bg-zinc-950/50 border border-zinc-800/80 rounded-xl space-y-3">
      <h4 className="text-[11px] font-bold text-zinc-300 uppercase tracking-wider flex items-center gap-1.5">
        <span className="text-indigo-400">{icon}</span>
        {title}
      </h4>
      {children}
    </div>
  );
}

function Figures({ children }: { children: React.ReactNode }) {
  return <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">{children}</div>;
}

function Figure({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="min-w-0">
      <p className="text-[9px] text-zinc-500 uppercase tracking-wider truncate" title={label}>{label}</p>
      <p className={`text-sm font-bold mt-0.5 ${tone || 'text-zinc-200'}`}>{value}</p>
    </div>
  );
}

function List({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <p className="text-[9px] text-zinc-600 uppercase tracking-wider font-semibold">{title}</p>
      <div className="divide-y divide-zinc-800/60">{children}</div>
    </div>
  );
}

function Row({ left, children }: { left: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1 text-[11px]">
      <span className="truncate">{left}</span>
      <span className="text-right whitespace-nowrap">{children}</span>
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="text-[11px] text-zinc-600 italic py-1">{children}</p>;
}
