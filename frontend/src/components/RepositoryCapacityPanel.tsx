import React, { useEffect, useState } from 'react';
import { AlertTriangle, Database, Info } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';
import { api } from '../api';
import { formatBytes } from './formatBytes';
import { InfoLabel } from './InfoLabel';
import type { RepositoryCapacity } from '../types';

/**
 * How full each borg repository's nightly window is, and whether more of them
 * would help.
 *
 * `BORG_SHARD_COUNT` decides how many backups can run at once — borg holds a
 * repository's lock for the whole of a backup — and nothing else in the
 * interface says whether the number in force is right. The only feedback
 * without this panel is a window that silently overruns, months later.
 *
 * Two things it must say plainly rather than imply, because both lead an
 * operator to the wrong action:
 *
 * - Raising the count moves nobody. A node's repository is fixed at enrolment,
 *   so a crowded repository stays crowded and only new enrolments go elsewhere.
 *   Past `CROWDED_PCT` the panel says so and points at rebalancing instead.
 * - Repositories multiply locks, not bandwidth. When the measured throughput
 *   ceiling is the binding constraint, adding repositories buys nothing at all.
 *
 * Fetched once on mount rather than polled: it reads a month of projection and
 * a quarter of history, and nothing in it changes minute to minute.
 */

/** Above this a repository has no useful room, and the advice changes. */
const CROWDED_PCT = 80;

function barColour(pct: number | null): string {
  if (pct === null) return 'bg-zinc-700';
  if (pct >= 100) return 'bg-rose-500';
  if (pct >= CROWDED_PCT) return 'bg-amber-500';
  return 'bg-emerald-500';
}

function formatHours(hours: number | null): string {
  if (hours === null) return '—';
  if (hours < 1) return `${Math.round(hours * 60)}m`;
  return `${hours.toFixed(1)}h`;
}

export default function RepositoryCapacityPanel() {
  const { t } = useTranslation();
  const [data, setData] = useState<RepositoryCapacity | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .get<RepositoryCapacity>('/api/stats/repository-capacity')
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch(() => {
        // A panel that cannot load is not a reason to break the settings page.
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (failed) {
    return (
      <div className="mb-4 p-4 border border-zinc-800/80 rounded-xl bg-zinc-950/40">
        <p className="text-xs text-zinc-500">{t('repoCapacityUnavailable')}</p>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="mb-4 p-4 border border-zinc-800/80 rounded-xl bg-zinc-950/40">
        <p className="text-xs text-zinc-500">{t('repoCapacityLoading')}</p>
      </div>
    );
  }

  const peakPct = data.peak.utilization_pct;
  const crowded = peakPct !== null && peakPct >= CROWDED_PCT;
  const storageBound = data.binding_constraint === 'storage_throughput';
  // Only the counts above the current one are a forecast; the first entry is
  // today, already shown above.
  const forecast = data.expansion.filter((e) => e.shard_count > data.shard_count);

  return (
    <div className="mb-4 space-y-3 border border-zinc-800/80 p-4 rounded-xl bg-zinc-950/40">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Database size={14} className="text-indigo-400 shrink-0" />
            <InfoLabel
              label={t('repoCapacityTitle')}
              hint={t('repoCapacityHint')}
              className="block text-xs font-bold text-zinc-300 uppercase tracking-wider"
            />
          </div>
          <span className="text-[10px] text-zinc-500 block mt-0.5">
            {t('repoCapacitySub')
              .replace('{count}', String(data.shard_count))
              .replace('{nights}', String(data.projection_nights))}
          </span>
        </div>

        <div className="text-right shrink-0">
          <div
            className={`text-2xl font-black tabular-nums ${
              peakPct === null
                ? 'text-zinc-600'
                : peakPct >= 100
                ? 'text-rose-400'
                : peakPct >= CROWDED_PCT
                ? 'text-amber-400'
                : 'text-emerald-400'
            }`}
          >
            {peakPct === null ? '—' : `${Math.round(peakPct)}%`}
          </div>
          <span className="text-[10px] text-zinc-500 uppercase tracking-wider">
            {t('repoCapacityPeakLabel')}
          </span>
        </div>
      </div>

      {data.count_floored && (
        <p className="text-[11px] text-amber-400/90 flex items-start gap-1.5">
          <Info size={12} className="mt-0.5 shrink-0" />
          {t('repoCapacityFloored')
            .replace('{configured}', String(data.configured_shard_count))
            .replace('{actual}', String(data.shard_count))}
        </p>
      )}

      {/* Per-repository load. The busiest night, because an average hides a
          repository that is idle six nights and overruns on the seventh. */}
      <div className="space-y-1.5">
        {data.shards.map((shard) => {
          const pct = shard.utilization_pct;
          return (
            <div key={shard.index} className="flex items-center gap-2.5 text-[11px]">
              <span className="w-16 shrink-0 font-mono text-zinc-400">
                {shard.index === 0 ? t('repoCapacityPrimary') : `shard-${shard.index}`}
              </span>
              <div className="flex-1 h-2 bg-zinc-900 rounded-full overflow-hidden border border-zinc-800/60">
                <div
                  className={`h-full ${barColour(pct)} transition-all`}
                  style={{ width: `${Math.min(100, pct ?? 0)}%` }}
                />
              </div>
              <span className="w-11 shrink-0 text-right tabular-nums text-zinc-300">
                {pct === null ? '—' : `${Math.round(pct)}%`}
              </span>
              <span className="w-28 shrink-0 text-right text-zinc-500 tabular-nums">
                {formatHours(shard.busiest_night_hours)} / {formatHours(shard.window_hours)}
              </span>
              <span className="w-24 shrink-0 text-right text-zinc-500 tabular-nums">
                {t('repoCapacityNodes').replace('{n}', String(shard.nodes))}
                {shard.size_bytes !== null && ` · ${formatBytes(shard.size_bytes)}`}
              </span>
              {!shard.initialized && (
                <span className="text-[9px] px-1.5 py-0.5 rounded bg-zinc-800/60 text-zinc-500 font-mono shrink-0">
                  {t('repoCapacityEmpty')}
                </span>
              )}
            </div>
          );
        })}
      </div>

      {/* What one repository holds. */}
      <div className="pt-2.5 border-t border-zinc-800/60 grid grid-cols-2 sm:grid-cols-3 gap-3 text-[11px]">
        <div>
          <div className="text-zinc-200 font-semibold tabular-nums">
            {data.capacity.sustained}
          </div>
          <span className="text-[10px] text-zinc-500">{t('repoCapacitySustained')}</span>
        </div>
        <div>
          <div className="text-zinc-200 font-semibold tabular-nums">
            {data.capacity.per_night}
          </div>
          <span className="text-[10px] text-zinc-500">{t('repoCapacityPerNight')}</span>
        </div>
        <div>
          <div className="text-zinc-200 font-semibold tabular-nums">
            {data.capacity.headroom_nodes}
          </div>
          <span className="text-[10px] text-zinc-500">{t('repoCapacityHeadroom')}</span>
        </div>
      </div>

      {/* The measured storage ceiling, or an honest statement that there is
          not enough history to have one. */}
      <div className="pt-2.5 border-t border-zinc-800/60 text-[11px]">
        {data.ceiling.sufficient ? (
          <p className={storageBound ? 'text-amber-400/90' : 'text-zinc-400'}>
            {t('repoCapacityCeilingMeasured')
              .replace('{mbps}', (data.ceiling.ceiling_mbps ?? 0).toFixed(0))
              .replace('{writers}', String(data.ceiling.max_observed_writers))}
            {storageBound && ` ${t('repoCapacityStorageBinds')
              .replace('{supported}', String(data.ceiling.supported_writers ?? 0))
              .replace('{count}', String(data.shard_count))}`}
          </p>
        ) : (
          <p className="text-zinc-500">{t('repoCapacityCeilingUnknown')}</p>
        )}

        {data.is_host_path && (
          <p className="text-zinc-500 mt-1">
            {t('repoCapacityNetworkStorage').replace('{path}', data.storage_path)}
          </p>
        )}
      </div>

      {/* Expansion. The headline of this section is what it does NOT do. */}
      {forecast.length > 0 && (
        <div className="pt-2.5 border-t border-zinc-800/60 space-y-1.5">
          <span className="text-[10px] font-semibold text-zinc-400 uppercase tracking-wider">
            {t('repoCapacityExpansionTitle')}
          </span>
          <div className="space-y-1">
            {forecast.map((outlook) => (
              <div
                key={outlook.shard_count}
                className="flex items-center justify-between text-[11px] text-zinc-400"
              >
                <span className="font-mono">
                  BORG_SHARD_COUNT={outlook.shard_count}
                </span>
                <span className="tabular-nums">
                  {t('repoCapacityExpansionRow')
                    .replace('{pct}', String(Math.round(outlook.busiest_utilization_pct ?? 0)))
                    .replace('{headroom}', String(outlook.new_node_headroom))}
                </span>
              </div>
            ))}
          </div>
          <p className="text-[10px] text-zinc-500 leading-relaxed">
            {t('repoCapacityExpansionCaveat')}
          </p>
        </div>
      )}

      {crowded && (
        <p className="text-[11px] text-rose-400/90 flex items-start gap-1.5 pt-2.5 border-t border-zinc-800/60">
          <AlertTriangle size={12} className="mt-0.5 shrink-0" />
          {t('repoCapacityCrowded')}
        </p>
      )}
    </div>
  );
}
