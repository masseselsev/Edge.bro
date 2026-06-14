import React, { useState, useEffect } from 'react';
import { Calendar, Plus, Edit2, Trash2, Play, Activity, RefreshCw } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';
import BackupGroupModal from './BackupGroupModal';
import type { BackupGroup } from './BackupGroupModal';

interface Node {
  id: number;
  hostname: string;
  group_id: number | null;
  backup_paused: boolean;
}

interface LoadData {
  day_load: number[];
  week_load: number[];
  month_load: number[];
}

export default function ScheduleTab() {
  const { t, language } = useTranslation();
  
  const [groups, setGroups] = useState<BackupGroup[]>([]);
  const [nodes, setNodes] = useState<Node[]>([]);
  const [loadData, setLoadData] = useState<LoadData>({
    day_load: Array(24).fill(0),
    week_load: Array(7).fill(0),
    month_load: Array(4).fill(0)
  });
  
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingGroup, setEditingGroup] = useState<BackupGroup | null>(null);

  // Threshold states for planned load maps
  const [dayThreshold, setDayThreshold] = useState<number>(() => {
    const saved = localStorage.getItem('scheduler_day_threshold');
    return saved ? Number(saved) : 10;
  });
  const [weekThreshold, setWeekThreshold] = useState<number>(() => {
    const saved = localStorage.getItem('scheduler_week_threshold');
    return saved ? Number(saved) : 100;
  });
  const [monthThreshold, setMonthThreshold] = useState<number>(() => {
    const saved = localStorage.getItem('scheduler_month_threshold');
    return saved ? Number(saved) : 400;
  });

  const handleDayThresholdChange = (val: number) => {
    const v = Math.max(1, val);
    setDayThreshold(v);
    localStorage.setItem('scheduler_day_threshold', String(v));
  };
  const handleWeekThresholdChange = (val: number) => {
    const v = Math.max(1, val);
    setWeekThreshold(v);
    localStorage.setItem('scheduler_week_threshold', String(v));
  };
  const handleMonthThresholdChange = (val: number) => {
    const v = Math.max(1, val);
    setMonthThreshold(v);
    localStorage.setItem('scheduler_month_threshold', String(v));
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
      if (nRes.ok) setNodes(await nRes.json());
      if (lRes.ok) setLoadData(await lRes.json());
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
  const getDayMarkerColor = (count: number) => {
    const ratio = count / dayThreshold;
    const hue = Math.max(0, 120 - ratio * 120);
    return `hsl(${hue}, 85%, 45%)`;
  };

  const getWeekMarkerColor = (count: number) => {
    const ratio = count / weekThreshold;
    const hue = Math.max(0, 120 - ratio * 120);
    return `hsl(${hue}, 85%, 45%)`;
  };

  const getMonthMarkerColor = (count: number) => {
    const ratio = count / monthThreshold;
    const hue = Math.max(0, 120 - ratio * 120);
    return `hsl(${hue}, 85%, 45%)`;
  };

  const getDayOfWeekName = (idx: number) => {
    const daysEn = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    const daysRu = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
    const daysUk = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Нд'];
    if (language === 'ru') return daysRu[idx];
    if (language === 'uk') return daysUk[idx];
    return daysEn[idx];
  };

  return (
    <div className="space-y-8 animate-fade-in p-6">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-slate-100 flex items-center gap-2">
            <Calendar className="h-6 w-6 text-indigo-400" />
            {t('tabSchedule')}
          </h2>
          <p className="text-slate-400 mt-1">
            Manage automated backup groups, scheduling policies, and execution windows.
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
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-md space-y-6">
        <h3 className="text-lg font-semibold text-slate-200 flex items-center gap-2 border-b border-slate-800 pb-3">
          <Activity className="h-5 w-5 text-indigo-400 animate-pulse" />
          {t('schedulerLoad')}
        </h3>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
          {/* Day Load Grid (24 Hour Markers) */}
          <div className="flex flex-col h-full space-y-3">
            <div className="flex justify-between items-center h-5">
              <span className="text-sm font-medium text-slate-300">{t('hourlyLoad')}</span>
              <button onClick={fetchData} className="text-slate-400 hover:text-slate-200">
                <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
              </button>
            </div>
            <div className="flex-1 flex flex-col justify-center p-4 bg-slate-950/50 rounded-lg border border-slate-800/80">
              <div className="grid grid-cols-8 gap-2.5 justify-items-center">
                {loadData.day_load.map((count, hr) => {
                  const color = getDayMarkerColor(count);
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
                      <div className="absolute bottom-full mb-1.5 hidden group-hover:block bg-slate-900 text-slate-100 text-xs py-1 px-2.5 rounded shadow-lg whitespace-nowrap border border-slate-800 z-10 font-mono">
                        {hr.toString().padStart(2, '0')}:00 - {count} {t('backups')}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
            <div className="flex justify-between items-center text-xs text-slate-500 font-mono h-5">
              <span>0 = Green</span>
              <div className="flex items-center gap-1.5">
                <input
                  type="number"
                  min={1}
                  value={dayThreshold}
                  onChange={(e) => handleDayThresholdChange(Number(e.target.value))}
                  className="w-12 px-1 py-0.5 bg-slate-950 border border-slate-800 rounded text-slate-350 text-center focus:outline-none focus:border-indigo-500 text-[11px] font-mono"
                />
                <span>= Red</span>
              </div>
            </div>
          </div>

          {/* Week Load Grid (7 Days) */}
          <div className="flex flex-col h-full space-y-3">
            <div className="flex justify-between items-center h-5">
              <span className="text-sm font-medium text-slate-300 block">{t('weeklyLoad')}</span>
            </div>
            <div className="flex-1 flex flex-col justify-center p-4 bg-slate-950/50 rounded-lg border border-slate-800/80">
              <div className="grid grid-cols-7 gap-2">
                {loadData.week_load.map((count, idx) => {
                  const color = getWeekMarkerColor(count);
                  return (
                    <div key={idx} className="group relative flex flex-col items-center gap-1.5">
                      <span className="text-xs text-slate-400 font-medium">{getDayOfWeekName(idx)}</span>
                      <div
                        className="h-20 w-full rounded-md flex items-center justify-center text-xs font-bold text-white transition-all hover:scale-105"
                        style={{ backgroundColor: color }}
                      >
                        {count}
                      </div>
                      <div className="absolute bottom-full mb-1.5 hidden group-hover:block bg-slate-900 text-slate-100 text-xs py-1 px-2.5 rounded shadow-lg whitespace-nowrap border border-slate-800 z-10 font-mono">
                        {getDayOfWeekName(idx)}: {count} {t('backups')}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
            <div className="flex justify-between items-center text-xs text-slate-500 font-mono h-5">
              <span>0 = Green</span>
              <div className="flex items-center gap-1.5">
                <input
                  type="number"
                  min={1}
                  value={weekThreshold}
                  onChange={(e) => handleWeekThresholdChange(Number(e.target.value))}
                  className="w-12 px-1 py-0.5 bg-slate-950 border border-slate-800 rounded text-slate-350 text-center focus:outline-none focus:border-indigo-500 text-[11px] font-mono"
                />
                <span>= Red</span>
              </div>
            </div>
          </div>

          {/* Month Load Grid (4 Weeks) */}
          <div className="flex flex-col h-full space-y-3">
            <div className="flex justify-between items-center h-5">
              <span className="text-sm font-medium text-slate-300 block">{t('monthlyLoad')}</span>
            </div>
            <div className="flex-1 flex flex-col justify-center p-4 bg-slate-950/50 rounded-lg border border-slate-800/80">
              <div className="grid grid-cols-4 gap-3">
                {loadData.month_load.map((count, idx) => {
                  const color = getMonthMarkerColor(count);
                  return (
                    <div key={idx} className="group relative flex flex-col items-center gap-1.5 w-full">
                      <span className="text-xs text-slate-400 font-medium">W{idx + 1}</span>
                      <div
                        className="w-full h-20 rounded-md flex items-center justify-center text-xs font-bold text-white transition-all hover:scale-105"
                        style={{ backgroundColor: color }}
                      >
                        {count}
                      </div>
                      <div className="absolute bottom-full mb-1.5 hidden group-hover:block bg-slate-900 text-slate-100 text-xs py-1 px-2.5 rounded shadow-lg whitespace-nowrap border border-slate-800 z-10 font-mono">
                        Week {idx + 1}: {count} {t('backups')}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
            <div className="flex justify-between items-center text-xs text-slate-500 font-mono h-5">
              <span>0 = Green</span>
              <div className="flex items-center gap-1.5">
                <input
                  type="number"
                  min={1}
                  value={monthThreshold}
                  onChange={(e) => handleMonthThresholdChange(Number(e.target.value))}
                  className="w-12 px-1 py-0.5 bg-slate-950 border border-slate-800 rounded text-slate-350 text-center focus:outline-none focus:border-indigo-500 text-[11px] font-mono"
                />
                <span>= Red</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Backup Groups Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
        {groups.map((group) => {
          const stats = getNodeStats(group.id);
          return (
            <div
              key={group.id}
              className="bg-slate-900 border border-slate-800 hover:border-slate-700/80 transition-all rounded-xl p-5 shadow-lg flex flex-col justify-between"
            >
              <div>
                <div className="flex justify-between items-start">
                  <h4 className="text-lg font-bold text-slate-100">{group.name}</h4>
                  <div className="flex gap-1">
                    <button
                      onClick={() => handleOpenEdit(group)}
                      className="p-1.5 text-slate-400 hover:text-indigo-400 rounded-md hover:bg-slate-800 transition"
                      title={t('editGroup')}
                    >
                      <Edit2 className="h-4 w-4" />
                    </button>
                    <button
                      onClick={() => handleDeleteGroup(group.id)}
                      className="p-1.5 text-slate-400 hover:text-rose-400 rounded-md hover:bg-slate-800 transition"
                      title={t('deleteNodeConfirm')}
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                </div>

                <div className="mt-4 space-y-2.5 text-sm">
                  <div className="flex justify-between">
                    <span className="text-slate-400">{t('interval')}:</span>
                    <span className="font-semibold text-slate-200 uppercase">{t(group.interval)}</span>
                  </div>
                  {group.interval !== 'weekly' && (
                    <div className="flex justify-between">
                      <span className="text-slate-400">{t('targetWeek')}:</span>
                      <span className="font-semibold text-slate-200">Week {group.target_week}</span>
                    </div>
                  )}
                  <div className="flex justify-between">
                    <span className="text-slate-400">Execution Window:</span>
                    <span className="font-semibold text-slate-200 font-mono">
                      {group.start_time} - {group.end_time}{' '}
                      <span className="text-xs text-slate-400 font-sans">
                        ({group.timezone === 'Browser Local' ? t('useBrowserLocal') : group.timezone})
                      </span>
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-400">{t('concurrencyLimit')}:</span>
                    <span className="font-semibold text-slate-200">{group.concurrency_limit} nodes</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-400">Distribution:</span>
                    <span className="font-semibold text-slate-200">
                      {group.randomize_days ? 'Staggered over Week' : 'Single Day Launch'}
                    </span>
                  </div>
                  <div className="flex flex-col gap-1 pt-1.5 border-t border-slate-800/50 mt-1.5">
                    <span className="text-xs text-slate-500 font-semibold uppercase">{t('retentionPolicy')}:</span>
                    <span className="text-xs font-medium text-slate-300">
                      {group.override_retention ? (
                        group.retention_policy ? (
                          group.retention_policy.type === 'interval' ? (
                            `Daily: ${group.retention_policy.keep_daily}, Weekly: ${group.retention_policy.keep_weekly}, Monthly: ${group.retention_policy.keep_monthly}`
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
                        <span className="text-slate-400 italic">{t('retentionInherit')}</span>
                      )}
                    </span>
                  </div>
                </div>
              </div>

              {/* Status and manual run */}
              <div className="mt-6 pt-4 border-t border-slate-800/80 flex items-center justify-between">
                <div className="flex gap-4">
                  <div className="text-xs">
                    <span className="text-slate-500 block uppercase font-semibold">Active Nodes</span>
                    <span className="text-emerald-400 font-bold text-sm">{stats.active}</span>
                  </div>
                  <div className="text-xs">
                    <span className="text-slate-500 block uppercase font-semibold">Paused Nodes</span>
                    <span className="text-amber-400 font-bold text-sm">{stats.paused}</span>
                  </div>
                </div>
                
                <button
                  onClick={() => handleBackupGroupNow(group.id)}
                  disabled={stats.active === 0}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold shadow transition duration-150 ${
                    stats.active > 0
                      ? 'bg-emerald-600/20 text-emerald-300 border border-emerald-500/30 hover:bg-emerald-600/30'
                      : 'bg-slate-800 text-slate-500 border border-slate-800 cursor-not-allowed'
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
          <div className="col-span-full border-2 border-dashed border-slate-850 bg-slate-900/30 rounded-xl p-8 text-center text-slate-400">
            No backup groups created yet. Click "{t('createGroup')}" in the top right to start scheduling.
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
