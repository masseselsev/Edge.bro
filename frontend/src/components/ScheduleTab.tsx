import React, { useState, useEffect } from 'react';
import { Calendar, Plus, Edit2, Trash2, Play, Activity, RefreshCw, AlertTriangle, CheckCircle2 } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';
import BackupGroupModal from './BackupGroupModal';
import type { BackupGroup } from './BackupGroupModal';
import { InfoLabel } from './InfoLabel';

interface Node {
  id: number;
  hostname: string;
  group_id: number | null;
  backup_paused: boolean;
  is_backup_running?: boolean;
  backup_progress?: number;
  backup_task_id?: string | null;
  last_ping_status?: boolean | null;
  last_available_at?: string | null;
}

interface GroupWindowFit {
  group_id: number;
  group_name: string;
  nodes_per_run: number;
  est_hours: number;
  window_hours: number;
  concurrency: number;
  capacity_hours: number;
  fits: boolean;
  rate_limit_kib: number | null;
  has_estimate: boolean;
}

interface LoadData {
  day_load: number[];
  week_load: number[];
  month_load: number[];
  day_hours: number[];
  week_hours: number[];
  month_hours: number[];
  group_fit: GroupWindowFit[];
}

const EMPTY_LOAD: LoadData = {
  day_load: Array(24).fill(0),
  week_load: Array(7).fill(0),
  month_load: Array(4).fill(0),
  day_hours: Array(24).fill(0),
  week_hours: Array(7).fill(0),
  month_hours: Array(4).fill(0),
  group_fit: []
};

type Metric = 'nodes' | 'hours';
type Bucket = 'day' | 'week' | 'month';

// A node count and an hour count need very different "this is red" points, so
// each metric keeps its own thresholds.
const THRESHOLD_DEFAULTS: Record<Metric, Record<Bucket, number>> = {
  nodes: { day: 10, week: 100, month: 400 },
  hours: { day: 8, week: 40, month: 160 }
};

// The 'nodes' keys predate the hours metric and are kept as-is.
const thresholdKey = (bucket: Bucket, metric: Metric) =>
  `scheduler_${bucket}_threshold${metric === 'hours' ? '_hours' : ''}`;

export default function ScheduleTab() {
  const { t, language } = useTranslation();

  const [groups, setGroups] = useState<BackupGroup[]>([]);
  const [nodes, setNodes] = useState<Node[]>([]);
  const [loadData, setLoadData] = useState<LoadData>(EMPTY_LOAD);

  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingGroup, setEditingGroup] = useState<BackupGroup | null>(null);

  const [metric, setMetric] = useState<Metric>(
    () => (localStorage.getItem('scheduler_load_metric') as Metric) || 'nodes'
  );

  const handleMetricChange = (m: Metric) => {
    setMetric(m);
    localStorage.setItem('scheduler_load_metric', m);
  };

  // Threshold states for planned load maps, keyed `${bucket}_${metric}`
  const [thresholds, setThresholds] = useState<Record<string, number>>(() => {
    const out: Record<string, number> = {};
    (['nodes', 'hours'] as Metric[]).forEach(m => {
      (['day', 'week', 'month'] as Bucket[]).forEach(b => {
        const saved = localStorage.getItem(thresholdKey(b, m));
        out[`${b}_${m}`] = saved ? Number(saved) : THRESHOLD_DEFAULTS[m][b];
      });
    });
    return out;
  });

  const thresholdFor = (bucket: Bucket) => thresholds[`${bucket}_${metric}`] || 1;

  const handleThresholdChange = (bucket: Bucket, val: number) => {
    const v = Math.max(1, val);
    setThresholds(prev => ({ ...prev, [`${bucket}_${metric}`]: v }));
    localStorage.setItem(thresholdKey(bucket, metric), String(v));
  };

  const fetchData = async () => {
    setLoading(true);
    try {
      const [gRes, nRes, lRes] = await Promise.all([
        fetch('/api/groups'),
        fetch('/api/nodes'),
        fetch('/api/groups/scheduler-load')
      ]);
      
      if (gRes.ok) setGroups(await gRes.json());
      if (nRes.ok) {
        const nData = await nRes.json();
        setNodes(Array.isArray(nData) ? nData : (nData.nodes || []));
      }
      // Merged onto the zeroed shape so an older backend that omits the hour
      // buckets renders empty maps instead of crashing on undefined.map().
      if (lRes.ok) setLoadData({ ...EMPTY_LOAD, ...(await lRes.json()) });
    } catch (err) {
      console.error("Failed to fetch scheduling data:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleOpenCreate = () => {
    setEditingGroup(null);
    setModalOpen(true);
  };

  const handleOpenEdit = (group: BackupGroup) => {
    setEditingGroup(group);
    setModalOpen(true);
  };

  const handleDeleteGroup = async (groupId: number) => {
    if (!window.confirm(t('deleteNodeConfirm'))) return; // Re-use delete confirmation
    try {
      const res = await fetch(`/api/groups/${groupId}`, { method: 'DELETE' });
      if (res.ok) {
        fetchData();
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleBackupGroupNow = async (groupId: number) => {
    try {
      const res = await fetch(`/api/groups/${groupId}/backup-now`, { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        alert(data.message);
      }
    } catch (err) {
      console.error(err);
    }
  };



  // Helper to count nodes in group
  const getNodeStats = (groupId: number) => {
    const groupNodes = nodes.filter(n => n.group_id === groupId);
    const active = groupNodes.filter(n => !n.backup_paused).length;
    const paused = groupNodes.filter(n => n.backup_paused).length;
    return { active, paused, total: groupNodes.length };
  };

  // HSL Hues Helper
  const getMarkerColor = (value: number, bucket: Bucket) => {
    const ratio = value / thresholdFor(bucket);
    const hue = Math.max(0, 120 - ratio * 120);
    return `hsl(${hue}, 85%, 45%)`;
  };

  const formatHours = (h: number) => {
    if (!h) return '0';
    if (h < 10) return h.toFixed(1);
    return String(Math.round(h));
  };

  /** The number drawn inside a cell, for whichever metric is selected. */
  const cellValue = (count: number, hours: number) =>
    metric === 'hours' ? formatHours(hours) : String(count);

  /** Tooltips always show both, so switching metric never hides information. */
  const cellDetail = (count: number, hours: number) =>
    `${count} ${t('backups')} · ${formatHours(hours)} ${t('hoursShort')}`;

  const seriesFor = (bucket: Bucket) => {
    const counts = bucket === 'day' ? loadData.day_load : bucket === 'week' ? loadData.week_load : loadData.month_load;
    const hours = bucket === 'day' ? loadData.day_hours : bucket === 'week' ? loadData.week_hours : loadData.month_hours;
    return counts.map((count, idx) => ({ count, hours: hours[idx] ?? 0 }));
  };

  const renderThresholdRow = (bucket: Bucket) => (
    <div className="flex justify-between items-center text-xs text-zinc-500 font-mono h-5">
      <span>0 = {t('greenColor')}</span>
      <div className="flex items-center gap-1.5">
        <input
          type="number"
          min={1}
          value={thresholdFor(bucket)}
          onChange={(e) => handleThresholdChange(bucket, Number(e.target.value))}
          className="w-12 px-1 py-0.5 bg-zinc-950 border border-zinc-800 rounded text-zinc-350 text-center focus:outline-none focus:border-indigo-500 text-[11px] font-mono"
        />
        <span>{metric === 'hours' ? `${t('hoursShort')} ` : ''}= {t('redColor')}</span>
      </div>
    </div>
  );

  const getDayOfWeekName = (idx: number) => {
    const daysEn = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    const daysRu = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
    const daysUk = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Нд'];
    if (language === 'ru') return daysRu[idx];
    if (language === 'uk') return daysUk[idx];
    return daysEn[idx];
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-zinc-100 flex items-center gap-2">
            <Calendar className="h-6 w-6 text-indigo-400" />
            {t('tabSchedule')}
          </h2>
          <p className="text-zinc-400 mt-1">
            {t('tabScheduleSub')}
          </p>
        </div>
        <button
          onClick={handleOpenCreate}
          className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg font-medium transition duration-150"
        >
          <Plus className="h-5 w-5" />
          {t('createGroup')}
        </button>
      </div>

      {/* Scheduler Planned Loads */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-6 shadow-md space-y-6">
        <div className="flex justify-between items-center border-b border-zinc-800 pb-3 gap-4 flex-wrap">
          <h3 className="text-lg font-semibold text-zinc-200 flex items-center gap-2">
            <Activity className="h-5 w-5 text-indigo-400 animate-pulse" />
            {t('schedulerLoad')}
          </h3>

          {/* Node counts hide the real cost on slow links, so the same maps can
              be read as estimated transfer hours instead. */}
          <div className="flex items-center gap-1 p-0.5 bg-zinc-950 border border-zinc-800 rounded-lg">
            {(['nodes', 'hours'] as Metric[]).map(m => (
              <button
                key={m}
                onClick={() => handleMetricChange(m)}
                className={`px-3 py-1 rounded-md text-xs font-semibold transition ${
                  metric === m
                    ? 'bg-indigo-600 text-white shadow'
                    : 'text-zinc-400 hover:text-zinc-200'
                }`}
              >
                {m === 'nodes' ? t('loadMetricNodes') : t('loadMetricHours')}
              </button>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
          {/* Day Load Grid (24 Hour Markers) */}
          <div className="flex flex-col h-full space-y-3">
            <div className="flex justify-between items-center h-5">
              <span className="text-sm font-medium text-zinc-300">{t('hourlyLoad')}</span>
              <button onClick={fetchData} className="text-zinc-400 hover:text-zinc-200">
                <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
              </button>
            </div>
            <div className="flex-1 flex flex-col justify-center p-4 bg-zinc-950/50 rounded-lg border border-zinc-800/80">
              <div className="grid grid-cols-8 gap-2.5 justify-items-center">
                {seriesFor('day').map(({ count, hours }, hr) => {
                  const color = getMarkerColor(metric === 'hours' ? hours : count, 'day');
                  return (
                    <div
                      key={hr}
                      className="group relative flex flex-col items-center"
                    >
                      <div
                        className="h-7 w-7 rounded-full flex items-center justify-center text-[10px] font-bold text-white transition-all hover:scale-110"
                        style={{ backgroundColor: color }}
                      >
                        {hr}
                      </div>
                      {/* Tooltip */}
                      <div className="absolute bottom-full mb-1.5 hidden group-hover:block bg-zinc-900 text-zinc-100 text-xs py-1 px-2.5 rounded shadow-lg whitespace-nowrap border border-zinc-800 z-10 font-mono">
                        {hr.toString().padStart(2, '0')}:00 - {cellDetail(count, hours)}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
            {renderThresholdRow('day')}
          </div>

          {/* Week Load Grid (7 Days) */}
          <div className="flex flex-col h-full space-y-3">
            <div className="flex justify-between items-center h-5">
              <span className="text-sm font-medium text-zinc-300 block">{t('weeklyLoad')}</span>
            </div>
            <div className="flex-1 flex flex-col justify-center p-4 bg-zinc-950/50 rounded-lg border border-zinc-800/80">
              <div className="grid grid-cols-7 gap-2">
                {seriesFor('week').map(({ count, hours }, idx) => {
                  const color = getMarkerColor(metric === 'hours' ? hours : count, 'week');
                  return (
                    <div key={idx} className="group relative flex flex-col items-center gap-1.5">
                      <span className="text-xs text-zinc-400 font-medium">{getDayOfWeekName(idx)}</span>
                      <div
                        className="h-20 w-full rounded-md flex items-center justify-center text-xs font-bold text-white transition-all hover:scale-105"
                        style={{ backgroundColor: color }}
                      >
                        {cellValue(count, hours)}
                      </div>
                      <div className="absolute bottom-full mb-1.5 hidden group-hover:block bg-zinc-900 text-zinc-100 text-xs py-1 px-2.5 rounded shadow-lg whitespace-nowrap border border-zinc-800 z-10 font-mono">
                        {getDayOfWeekName(idx)}: {cellDetail(count, hours)}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
            {renderThresholdRow('week')}
          </div>

          {/* Month Load Grid (4 Weeks) */}
          <div className="flex flex-col h-full space-y-3">
            <div className="flex justify-between items-center h-5">
              <span className="text-sm font-medium text-zinc-300 block">{t('monthlyLoad')}</span>
            </div>
            <div className="flex-1 flex flex-col justify-center p-4 bg-zinc-950/50 rounded-lg border border-zinc-800/80">
              <div className="grid grid-cols-4 gap-3">
                {seriesFor('month').map(({ count, hours }, idx) => {
                  const color = getMarkerColor(metric === 'hours' ? hours : count, 'month');
                  return (
                    <div key={idx} className="group relative flex flex-col items-center gap-1.5 w-full">
                      <span className="text-xs text-zinc-400 font-medium">W{idx + 1}</span>
                      <div
                        className="w-full h-20 rounded-md flex items-center justify-center text-xs font-bold text-white transition-all hover:scale-105"
                        style={{ backgroundColor: color }}
                      >
                        {cellValue(count, hours)}
                      </div>
                      <div className="absolute bottom-full mb-1.5 hidden group-hover:block bg-zinc-900 text-zinc-100 text-xs py-1 px-2.5 rounded shadow-lg whitespace-nowrap border border-zinc-800 z-10 font-mono">
                        {t('weekUnit')} {idx + 1}: {cellDetail(count, hours)}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
            {renderThresholdRow('month')}
          </div>
        </div>

        {/* Does each group's busiest day actually fit its execution window? */}
        <div className="pt-4 border-t border-zinc-800 space-y-2.5">
          <InfoLabel
            label={t('windowFitTitle')}
            hint={t('windowFitHint')}
            className="block text-sm font-medium text-zinc-300 mb-1"
          />

          {loadData.group_fit.length === 0 ? (
            <p className="text-xs text-zinc-500">{t('windowFitEmpty')}</p>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2.5">
              {loadData.group_fit.map(fit => (
                <div
                  key={fit.group_id}
                  className={`p-3 rounded-lg border text-xs ${
                    fit.fits
                      ? 'bg-zinc-950/50 border-zinc-800/80'
                      : 'bg-rose-500/5 border-rose-500/30'
                  }`}
                >
                  <div className="flex items-center gap-1.5 font-semibold">
                    {fit.fits ? (
                      <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400 shrink-0" />
                    ) : (
                      <AlertTriangle className="h-3.5 w-3.5 text-rose-400 shrink-0" />
                    )}
                    <span className="text-zinc-200 truncate">{fit.group_name}</span>
                    <span className={`ml-auto shrink-0 ${fit.fits ? 'text-emerald-400' : 'text-rose-400'}`}>
                      {fit.fits ? t('windowFitOk') : t('windowFitOver')}
                    </span>
                  </div>
                  <p className="mt-1.5 text-zinc-400 leading-relaxed">
                    {t('windowFitDetail')
                      .replace('{est}', formatHours(fit.est_hours))
                      .replace('{capacity}', formatHours(fit.capacity_hours))
                      .replace('{window}', formatHours(fit.window_hours))
                      .replace('{conc}', String(fit.concurrency))}
                  </p>
                  {!fit.has_estimate && (
                    <p className="mt-1 text-[10px] text-amber-400/80">{t('windowFitNoEstimate')}</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Backup Groups Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
        {groups.map((group) => {
          const stats = getNodeStats(group.id);
          return (
            <div
              key={group.id}
              className="bg-zinc-900 border border-zinc-800 hover:border-zinc-700/80 transition-all rounded-2xl p-5 shadow-lg flex flex-col justify-between"
            >
              <div>
                <div className="flex justify-between items-start">
                  <h4 className="text-lg font-bold text-zinc-100">{group.name}</h4>
                  <div className="flex gap-1">
                    <button
                      onClick={() => handleOpenEdit(group)}
                      className="p-1.5 text-zinc-400 hover:text-indigo-400 rounded-md hover:bg-zinc-800 transition"
                      title={t('editGroup')}
                    >
                      <Edit2 className="h-4 w-4" />
                    </button>
                    <button
                      onClick={() => handleDeleteGroup(group.id)}
                      className="p-1.5 text-zinc-400 hover:text-rose-400 rounded-md hover:bg-zinc-800 transition"
                      title={t('deleteNodeConfirm')}
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                </div>

                <div className="mt-4 space-y-2.5 text-sm">
                  <div className="flex justify-between">
                    <span className="text-zinc-400">{t('interval')}:</span>
                    <span className="font-semibold text-zinc-200 uppercase">{t(group.interval)}</span>
                  </div>
                  {group.interval !== 'weekly' && group.interval !== '10min' && group.interval !== '30min' && (
                    <div className="flex justify-between">
                      <span className="text-zinc-400">{t('targetWeek')}:</span>
                      <span className="font-semibold text-zinc-200">{t('weekUnit')} {group.target_week}</span>
                    </div>
                  )}
                  <div className="flex justify-between">
                    <span className="text-zinc-400">{t('executionWindow')}:</span>
                    <span className="font-semibold text-zinc-200 font-mono">
                      {group.start_time} - {group.end_time}{' '}
                      <span className="text-xs text-zinc-400 font-sans">
                        ({group.timezone === 'Browser Local' ? t('useBrowserLocal') : group.timezone})
                      </span>
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-zinc-400">{t('concurrencyLimit')}:</span>
                    <span className="font-semibold text-zinc-200">{group.concurrency_limit} {t('nodesUnit')}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-zinc-400">{t('distribution')}:</span>
                    <span className="font-semibold text-zinc-200">
                      {group.randomize_days ? t('staggeredOverWeek') : t('singleDayLaunch')}
                    </span>
                  </div>
                  <div className="flex flex-col gap-1 pt-1.5 border-t border-zinc-800/50 mt-1.5">
                    <span className="text-xs text-zinc-500 font-semibold uppercase">{t('retentionPolicy')}:</span>
                    <span className="text-xs font-medium text-zinc-300">
                      {group.override_retention ? (
                        group.retention_policy ? (
                          group.retention_policy.type === 'interval' ? (
                            `${t('keepDaily')}: ${group.retention_policy.keep_daily}, ${t('keepWeekly')}: ${group.retention_policy.keep_weekly}, ${t('keepMonthly')}: ${group.retention_policy.keep_monthly}`
                          ) : group.retention_policy.type === 'count' ? (
                            t('retentionSummaryLast').replace('{count}', String(group.retention_policy.keep_last))
                          ) : (
                            t('retentionSummaryWithin')
                              .replace('{value}', String(group.retention_policy.within_value))
                              .replace('{unit}', t(
                                group.retention_policy.within_unit === 'd' ? 'timeframeUnitDays' :
                                group.retention_policy.within_unit === 'w' ? 'timeframeUnitWeeks' :
                                group.retention_policy.within_unit === 'm' ? 'timeframeUnitMonths' :
                                'timeframeUnitYears'
                              ).toLowerCase())
                          )
                        ) : 'None'
                      ) : (
                        <span className="text-zinc-400 italic">{t('retentionInherit')}</span>
                      )}
                    </span>
                  </div>
                </div>
              </div>

              {/* Status and manual run */}
              <div className="mt-6 pt-4 border-t border-zinc-800/80 flex items-center justify-between">
                <div className="flex gap-4">
                  <div className="text-xs">
                    <span className="text-zinc-500 block uppercase font-semibold">{t('activeNodes')}</span>
                    <span className="text-emerald-400 font-bold text-sm">{stats.active}</span>
                  </div>
                  <div className="text-xs">
                    <span className="text-zinc-500 block uppercase font-semibold">{t('pausedNodes')}</span>
                    <span className="text-amber-400 font-bold text-sm">{stats.paused}</span>
                  </div>
                </div>
                
                <button
                  onClick={() => handleBackupGroupNow(group.id)}
                  disabled={stats.active === 0}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold shadow transition duration-150 ${
                    stats.active > 0
                      ? 'bg-emerald-600/20 text-emerald-300 border border-emerald-500/30 hover:bg-emerald-600/30'
                      : 'bg-zinc-800 text-zinc-500 border border-zinc-800 cursor-not-allowed'
                  }`}
                >
                  <Play className="h-3.5 w-3.5 fill-current" />
                  {t('groupBackupNow')}
                </button>
              </div>
            </div>
          );
        })}

        {groups.length === 0 && (
          <div className="col-span-full border-2 border-dashed border-zinc-850 bg-zinc-900/30 rounded-xl p-8 text-center text-zinc-400">
            {t('noGroupsCreated')}
          </div>
        )}
      </div>

      <BackupGroupModal
        isOpen={modalOpen}
        onClose={() => setModalOpen(false)}
        onSaved={fetchData}
        editingGroup={editingGroup}
      />
    </div>
  );
}
