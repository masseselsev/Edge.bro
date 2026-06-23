import React, { useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { Calendar, X } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';
import { SearchableSelect } from './SearchableSelect';
import type { Option } from './SearchableSelect';

export interface BackupGroup {
  id: number;
  name: string;
  interval: string;
  target_week: number;
  start_time: string;
  end_time: string;
  concurrency_limit: number;
  randomize_days: boolean;
  timezone: string;
  override_retention?: boolean;
  retention_policy?: {
    type: string;
    keep_daily: number;
    keep_weekly: number;
    keep_monthly: number;
    keep_last: number;
    within_value: number;
    within_unit: string;
  } | null;
  upload_rate_limit?: number | null;
  compression?: string | null;
  checkpoint_interval?: number | null;
  cpu_quota?: number | null;
}

interface BackupGroupModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSaved: () => void;
  editingGroup: BackupGroup | null;
}

export default function BackupGroupModal({ isOpen, onClose, onSaved, editingGroup }: BackupGroupModalProps) {
  const { t } = useTranslation();
  
  // Form state
  const [name, setName] = useState('');
  const [interval, setIntervalVal] = useState('weekly');
  const [targetWeek, setTargetWeek] = useState(1);
  const [startTime, setStartTime] = useState('02:00');
  const [endTime, setEndTime] = useState('05:00');
  const [concurrencyLimit, setConcurrencyLimit] = useState(5);
  const [randomizeDays, setRandomizeDays] = useState(true);
  const [useLocalTime, setUseLocalTime] = useState(false);
  const [timezone, setTimezone] = useState('UTC');
  const [overrideRetention, setOverrideRetention] = useState(false);
  const [policyType, setPolicyType] = useState<'interval' | 'count' | 'timeframe'>('interval');
  const [policyKeepDaily, setPolicyKeepDaily] = useState(7);
  const [policyKeepWeekly, setPolicyKeepWeekly] = useState(4);
  const [policyKeepMonthly, setPolicyKeepMonthly] = useState(6);
  const [policyKeepLast, setPolicyKeepLast] = useState(5);
  const [policyWithinValue, setPolicyWithinValue] = useState(3);
  const [policyWithinUnit, setPolicyWithinUnit] = useState<'d' | 'w' | 'm' | 'y'>('m');
  const [uploadRateLimit, setUploadRateLimit] = useState<number | ''>('');
  const [compression, setCompression] = useState<string>('');
  const [checkpointInterval, setCheckpointInterval] = useState<number | ''>('');
  const [cpuQuota, setCpuQuota] = useState<number | ''>('');
  const [error, setError] = useState('');

  // Generate timezone options
  const timezoneOptions: Option[] = React.useMemo(() => {
    let zones: string[] = [];
    try {
      zones = (Intl as any).supportedValuesOf('timeZone') || [];
    } catch (e) {
      zones = [
        'UTC', 'Europe/Moscow', 'Europe/London', 'Europe/Paris', 
        'America/New_York', 'America/Los_Angeles', 'Asia/Tokyo', 
        'Asia/Shanghai', 'Asia/Kolkata', 'Asia/Yekaterinburg'
      ];
    }
    return zones.map(tz => ({
      value: tz,
      label: tz
    }));
  }, []);

  const intervalOptions = React.useMemo(() => [
    { value: 'weekly', label: t('weekly') },
    { value: 'monthly', label: t('monthly') },
    { value: 'quarterly', label: t('quarterly') },
    { value: 'yearly', label: t('yearly') }
  ], [t]);

  const targetWeekOptions = React.useMemo(() => [
    { value: 1, label: `${t('weekUnit')} 1` },
    { value: 2, label: `${t('weekUnit')} 2` },
    { value: 3, label: `${t('weekUnit')} 3` },
    { value: 4, label: `${t('weekUnit')} 4` }
  ], [t]);

  const policyTypeOptions = React.useMemo(() => [
    { value: 'interval', label: t('policyInterval') },
    { value: 'count', label: t('policyCount') },
    { value: 'timeframe', label: t('policyTimeframe') }
  ], [t]);

  const unitOptions = React.useMemo(() => [
    { value: 'd', label: t('timeframeUnitDays') },
    { value: 'w', label: t('timeframeUnitWeeks') },
    { value: 'm', label: t('timeframeUnitMonths') },
    { value: 'y', label: t('timeframeUnitYears') }
  ], [t]);

  const compressionOptions = React.useMemo(() => [
    { value: '', label: t('compressionGlobalDefault') },
    { value: 'none', label: 'none' },
    { value: 'lz4', label: 'lz4' },
    { value: 'zstd:1', label: 'zstd:1' },
    { value: 'zstd:3', label: 'zstd:3' },
    { value: 'zstd:5', label: 'zstd:5' },
    { value: 'zstd:9', label: 'zstd:9' }
  ], [t]);

  useEffect(() => {
    if (editingGroup) {
      setName(editingGroup.name);
      setIntervalVal(editingGroup.interval);
      setTargetWeek(editingGroup.target_week);
      setStartTime(editingGroup.start_time);
      setEndTime(editingGroup.end_time);
      setConcurrencyLimit(editingGroup.concurrency_limit);
      setRandomizeDays(editingGroup.randomize_days);
      setOverrideRetention(!!editingGroup.override_retention);
      
      const rp = editingGroup.retention_policy;
      if (rp) {
        setPolicyType(rp.type as any || 'interval');
        setPolicyKeepDaily(rp.keep_daily ?? 7);
        setPolicyKeepWeekly(rp.keep_weekly ?? 4);
        setPolicyKeepMonthly(rp.keep_monthly ?? 6);
        setPolicyKeepLast(rp.keep_last ?? 5);
        setPolicyWithinValue(rp.within_value ?? 3);
        setPolicyWithinUnit(rp.within_unit as any || 'm');
      } else {
        setPolicyType('interval');
        setPolicyKeepDaily(7);
        setPolicyKeepWeekly(4);
        setPolicyKeepMonthly(6);
        setPolicyKeepLast(5);
        setPolicyWithinValue(3);
        setPolicyWithinUnit('m');
      }
      
      const gTz = editingGroup.timezone || 'UTC';
      if (gTz === 'Browser Local') {
        setUseLocalTime(true);
        try {
          setTimezone(Intl.DateTimeFormat().resolvedOptions().timeZone || 'Europe/Moscow');
        } catch (e) {
          setTimezone('Europe/Moscow');
        }
      } else {
        setUseLocalTime(false);
        setTimezone(gTz);
      }

      setUploadRateLimit(editingGroup.upload_rate_limit ?? '');
      setCompression(editingGroup.compression ?? '');
      setCheckpointInterval(editingGroup.checkpoint_interval ?? '');
      setCpuQuota(editingGroup.cpu_quota ?? '');
    } else {
      setName('');
      setIntervalVal('weekly');
      setTargetWeek(1);
      setStartTime('02:00');
      setEndTime('05:00');
      setConcurrencyLimit(5);
      setRandomizeDays(true);
      setUseLocalTime(false);
      setTimezone('UTC');
      setOverrideRetention(false);
      setPolicyType('interval');
      setPolicyKeepDaily(7);
      setPolicyKeepWeekly(4);
      setPolicyKeepMonthly(6);
      setPolicyKeepLast(5);
      setPolicyWithinValue(3);
      setPolicyWithinUnit('m');
      setUploadRateLimit('');
      setCompression('');
      setCheckpointInterval('');
      setCpuQuota('');
    }
    setError('');
  }, [editingGroup, isOpen]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    
    const payload = {
      name,
      interval,
      target_week: targetWeek,
      start_time: startTime,
      end_time: endTime,
      concurrency_limit: concurrencyLimit,
      randomize_days: randomizeDays,
      timezone: useLocalTime ? 'Browser Local' : timezone,
      override_retention: overrideRetention,
      retention_policy: overrideRetention ? {
        type: policyType,
        keep_daily: policyKeepDaily,
        keep_weekly: policyKeepWeekly,
        keep_monthly: policyKeepMonthly,
        keep_last: policyKeepLast,
        within_value: policyWithinValue,
        within_unit: policyWithinUnit
      } : null,
      upload_rate_limit: uploadRateLimit === '' ? null : Number(uploadRateLimit),
      compression: compression === '' ? null : compression,
      checkpoint_interval: checkpointInterval === '' ? null : Number(checkpointInterval),
      cpu_quota: cpuQuota === '' ? null : Number(cpuQuota)
    };

    try {
      const url = editingGroup ? `/api/groups/${editingGroup.id}` : '/api/groups';
      const method = editingGroup ? 'PUT' : 'POST';
      
      const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      if (res.ok) {
        onSaved();
        onClose();
      } else {
        const errData = await res.json();
        setError(errData.detail || "An error occurred.");
      }
    } catch (err) {
      setError("Failed to save group.");
    }
  };

  if (!isOpen) return null;

  return createPortal(
    <div className="fixed inset-0 bg-zinc-950/80 backdrop-blur-sm z-50 flex items-center justify-center p-4 animate-fade-in">
      <div className="bg-zinc-900 border border-zinc-800 rounded-2xl w-full max-w-3xl shadow-2xl overflow-hidden animate-modal-in">
        <div className="flex justify-between items-center p-5 border-b border-zinc-800">
          <h3 className="text-lg font-bold text-zinc-100 flex items-center gap-2">
            <Calendar className="h-5 w-5 text-indigo-400" />
            {editingGroup ? t('editGroup') : t('createGroup')}
          </h3>
          <button onClick={onClose} className="text-zinc-400 hover:text-zinc-200">
            <X className="h-5 w-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col max-h-[85vh]">
          <div className="p-5 space-y-4 overflow-y-auto flex-1">
            {error && (
              <div className="bg-rose-500/10 border border-rose-500/20 text-rose-300 text-sm p-3 rounded-lg">
                {error}
              </div>
            )}

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 items-start">
              {/* Left Column: General Settings & Scheduling */}
              <div className="space-y-4">
                <div>
                  <label className="block text-xs font-bold uppercase tracking-wider text-zinc-400 mb-1.5">
                    {t('groupName')}
                  </label>
                  <input
                    type="text"
                    required
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 placeholder-zinc-655 focus:outline-none focus:border-indigo-500 text-sm"
                    placeholder={t('groupNamePlaceholder')}
                  />
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs font-bold uppercase tracking-wider text-zinc-400 mb-1.5">
                      {t('interval')}
                    </label>
                    <SearchableSelect
                      options={intervalOptions}
                      value={interval}
                      onChange={setIntervalVal}
                      placeholder={t('selectIntervalPlaceholder')}
                    />
                  </div>

                  {interval !== 'weekly' && (
                    <div>
                      <label className="block text-xs font-bold uppercase tracking-wider text-zinc-400 mb-1.5">
                        {t('targetWeek')}
                      </label>
                      <SearchableSelect
                        options={targetWeekOptions}
                        value={targetWeek}
                        onChange={(val) => setTargetWeek(Number(val))}
                        placeholder={t('selectWeekPlaceholder')}
                      />
                    </div>
                  )}
                </div>

                <div>
                  <div className="flex justify-between items-center mb-1.5 min-h-[16px]">
                    <label className="block text-xs font-bold uppercase tracking-wider text-zinc-400">
                      {t('groupTimezone')}
                    </label>
                    <div className="flex items-center gap-1.5">
                      <input
                        type="checkbox"
                        id="useLocalTimeGroup"
                        checked={useLocalTime}
                        onChange={(e) => {
                          const checked = e.target.checked;
                          setUseLocalTime(checked);
                          if (checked) {
                            try {
                              const localTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
                              if (localTz) {
                                setTimezone(localTz);
                              }
                            } catch (err) {}
                          }
                        }}
                        className="rounded border-zinc-800 bg-zinc-950 text-indigo-600 focus:ring-indigo-500 h-3.5 w-3.5 cursor-pointer"
                      />
                      <label htmlFor="useLocalTimeGroup" className="text-[10px] font-bold text-zinc-500 hover:text-zinc-400 transition-colors uppercase tracking-wider cursor-pointer select-none">
                        {t('useBrowserLocal')}
                      </label>
                    </div>
                  </div>
                  <SearchableSelect
                    options={timezoneOptions}
                    value={timezone}
                    onChange={setTimezone}
                    disabled={useLocalTime}
                    placeholder={t('selectTimezone')}
                  />
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs font-bold uppercase tracking-wider text-zinc-400 mb-1.5">
                      {t('startTime')}
                    </label>
                    <input
                      type="time"
                      required
                      value={startTime}
                      onChange={(e) => setStartTime(e.target.value)}
                      className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:outline-none focus:border-indigo-500"
                    />
                  </div>

                  <div>
                    <label className="block text-xs font-bold uppercase tracking-wider text-zinc-400 mb-1.5">
                      {t('endTime')}
                    </label>
                    <input
                      type="time"
                      required
                      value={endTime}
                      onChange={(e) => setEndTime(e.target.value)}
                      className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:outline-none focus:border-indigo-500"
                    />
                  </div>
                </div>

                <div>
                  <label className="block text-xs font-bold uppercase tracking-wider text-zinc-400 mb-1.5">
                    {t('concurrencyLimit')}
                  </label>
                  <input
                    type="number"
                    required
                    min={1}
                    max={20}
                    value={concurrencyLimit}
                    onChange={(e) => setConcurrencyLimit(Number(e.target.value))}
                    className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:outline-none focus:border-indigo-500 font-mono"
                  />
                </div>

                <div className="flex items-center gap-3 pt-2">
                  <input
                    type="checkbox"
                    id="randomize_days"
                    checked={randomizeDays}
                    onChange={(e) => setRandomizeDays(e.target.checked)}
                    className="h-4.5 w-4.5 text-indigo-600 focus:ring-indigo-500 border-zinc-800 rounded bg-zinc-950"
                  />
                  <label htmlFor="randomize_days" className="text-sm font-medium text-zinc-350 cursor-pointer">
                    {t('randomizeDays')}
                  </label>
                </div>
              </div>

              {/* Right Column: Retention & Resource Limits */}
              <div className="space-y-4">
                {/* Retention Panel */}
                <div className="p-4 bg-zinc-950/30 border border-zinc-800/80 rounded-xl space-y-3">
                  <div className="flex items-center gap-3">
                    <input
                      type="checkbox"
                      id="override_retention"
                      checked={overrideRetention}
                      onChange={(e) => setOverrideRetention(e.target.checked)}
                      className="h-4.5 w-4.5 text-indigo-600 focus:ring-indigo-500 border-zinc-800 rounded bg-zinc-950"
                    />
                    <label htmlFor="override_retention" className="text-sm font-bold text-zinc-200 cursor-pointer">
                      {t('overrideRetention')}
                    </label>
                  </div>

                  {overrideRetention && (
                    <div className="space-y-3 pt-1 animate-fade-in">
                      <div>
                        <label className="block text-xs font-bold uppercase tracking-wider text-zinc-400 mb-1">{t('retentionType')}</label>
                        <SearchableSelect
                          options={policyTypeOptions}
                          value={policyType}
                          onChange={setPolicyType}
                          placeholder={t('selectPolicyTypePlaceholder')}
                        />
                      </div>

                      {policyType === 'interval' && (
                        <div className="grid grid-cols-3 gap-2">
                          <div>
                            <label className="block text-[10px] font-bold uppercase tracking-wider text-zinc-500 mb-1">{t('keepDaily')}</label>
                            <input
                              type="number"
                              required
                              min={0}
                              value={policyKeepDaily}
                              onChange={(e) => setPolicyKeepDaily(parseInt(e.target.value) || 0)}
                              className="w-full px-2 py-1.5 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-105 text-xs text-center focus:outline-none focus:border-indigo-500 font-mono"
                            />
                          </div>
                          <div>
                            <label className="block text-[10px] font-bold uppercase tracking-wider text-zinc-500 mb-1">{t('keepWeekly')}</label>
                            <input
                              type="number"
                              required
                              min={0}
                              value={policyKeepWeekly}
                              onChange={(e) => setPolicyKeepWeekly(parseInt(e.target.value) || 0)}
                              className="w-full px-2 py-1.5 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-105 text-xs text-center focus:outline-none focus:border-indigo-500 font-mono"
                            />
                          </div>
                          <div>
                            <label className="block text-[10px] font-bold uppercase tracking-wider text-zinc-500 mb-1">{t('keepMonthly')}</label>
                            <input
                              type="number"
                              required
                              min={0}
                              value={policyKeepMonthly}
                              onChange={(e) => setPolicyKeepMonthly(parseInt(e.target.value) || 0)}
                              className="w-full px-2 py-1.5 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-105 text-xs text-center focus:outline-none focus:border-indigo-500 font-mono"
                            />
                          </div>
                        </div>
                      )}

                      {policyType === 'count' && (
                        <div>
                          <label className="block text-xs font-bold uppercase tracking-wider text-zinc-400 mb-1">{t('keepLastLabel')}</label>
                          <input
                            type="number"
                            required
                            min={1}
                            value={policyKeepLast}
                            onChange={(e) => setPolicyKeepLast(parseInt(e.target.value) || 1)}
                            className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-xs focus:outline-none focus:border-indigo-500 font-mono"
                          />
                        </div>
                      )}

                      {policyType === 'timeframe' && (
                        <div className="grid grid-cols-3 gap-2">
                          <div className="col-span-2">
                            <label className="block text-xs font-bold uppercase tracking-wider text-zinc-400 mb-1">{t('keepWithinLabel')}</label>
                            <input
                              type="number"
                              required
                              min={1}
                              value={policyWithinValue}
                              onChange={(e) => setPolicyWithinValue(parseInt(e.target.value) || 1)}
                              className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-xs focus:outline-none focus:border-indigo-500 font-mono"
                            />
                          </div>
                          <div>
                            <label className="block text-xs font-bold uppercase tracking-wider text-zinc-400 mb-1">&nbsp;</label>
                            <SearchableSelect
                              options={unitOptions}
                              value={policyWithinUnit}
                              onChange={setPolicyWithinUnit}
                              placeholder={t('selectUnitPlaceholder')}
                            />
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {/* Resource Limits Panel */}
                <div className="p-4 bg-zinc-950/30 border border-zinc-800/80 rounded-xl space-y-3">
                  <h4 className="text-xs font-bold text-zinc-200 uppercase tracking-wider pb-1 border-b border-zinc-800/60">
                    {t('resourceLimits')}
                  </h4>

                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-[10px] font-bold uppercase tracking-wider text-zinc-400 mb-1">
                        {t('uploadRateLimit')}
                      </label>
                      <input
                        type="number"
                        min={0}
                        value={uploadRateLimit}
                        onChange={(e) => setUploadRateLimit(e.target.value === '' ? '' : Number(e.target.value))}
                        className="w-full px-3 py-1.5 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-xs focus:outline-none focus:border-indigo-500 font-mono"
                        placeholder="e.g. 250"
                      />
                      <p className="text-[9px] text-zinc-500 mt-1 leading-normal">
                        {t('uploadRateLimitHint')}
                      </p>
                    </div>

                    <div>
                      <label className="block text-[10px] font-bold uppercase tracking-wider text-zinc-400 mb-1">
                        {t('compressionMode')}
                      </label>
                      <SearchableSelect
                        options={compressionOptions}
                        value={compression}
                        onChange={setCompression}
                        placeholder={t('selectCompressionPlaceholder')}
                      />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-[10px] font-bold uppercase tracking-wider text-zinc-400 mb-1">
                        {t('checkpointInterval')}
                      </label>
                      <input
                        type="number"
                        min={0}
                        value={checkpointInterval}
                        onChange={(e) => setCheckpointInterval(e.target.value === '' ? '' : Number(e.target.value))}
                        className="w-full px-3 py-1.5 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-xs focus:outline-none focus:border-indigo-500 font-mono"
                        placeholder="e.g. 1800"
                      />
                      <p className="text-[9px] text-zinc-500 mt-1 leading-normal">
                        {t('checkpointAuto')}
                      </p>
                    </div>

                    <div>
                      <label className="block text-[10px] font-bold uppercase tracking-wider text-zinc-400 mb-1">
                        {t('cpuQuota')}
                      </label>
                      <input
                        type="number"
                        min={0}
                        max={400}
                        value={cpuQuota}
                        onChange={(e) => setCpuQuota(e.target.value === '' ? '' : Number(e.target.value))}
                        className="w-full px-3 py-1.5 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-xs focus:outline-none focus:border-indigo-500 font-mono"
                        placeholder="e.g. 50"
                      />
                      <p className="text-[9px] text-zinc-500 mt-1 leading-normal">
                        {t('cpuQuotaHint')}
                      </p>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div className="flex justify-end gap-3 p-5 border-t border-zinc-800 bg-zinc-900/50">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded-lg font-medium transition duration-150"
            >
              {t('cancel')}
            </button>
            <button
              type="submit"
              className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg font-medium transition duration-150"
            >
              {t('saveAndContinue')}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body
  );
}
