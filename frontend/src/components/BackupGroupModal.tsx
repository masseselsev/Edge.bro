import React, { useState, useEffect } from 'react';
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

  useEffect(() => {
    if (editingGroup) {
      setName(editingGroup.name);
      setIntervalVal(editingGroup.interval);
      setTargetWeek(editingGroup.target_week);
      setStartTime(editingGroup.start_time);
      setEndTime(editingGroup.end_time);
      setConcurrencyLimit(editingGroup.concurrency_limit);
      setRandomizeDays(editingGroup.randomize_days);
      
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
      timezone: useLocalTime ? 'Browser Local' : timezone
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

  return (
    <div className="fixed inset-0 bg-slate-950/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
      <div className="bg-slate-900 border border-slate-800 rounded-xl w-full max-w-md shadow-2xl overflow-hidden animate-modal-in">
        <div className="flex justify-between items-center p-5 border-b border-slate-800">
          <h3 className="text-lg font-bold text-slate-100 flex items-center gap-2">
            <Calendar className="h-5 w-5 text-indigo-400" />
            {editingGroup ? t('editGroup') : t('createGroup')}
          </h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-200">
            <X className="h-5 w-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-5 space-y-4">
          {error && (
            <div className="bg-rose-500/10 border border-rose-500/20 text-rose-300 text-sm p-3 rounded-lg">
              {error}
            </div>
          )}

          <div>
            <label className="block text-xs font-bold uppercase tracking-wider text-slate-400 mb-1.5">
              {t('groupName')}
            </label>
            <input
              type="text"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-slate-100 placeholder-slate-655 focus:outline-none focus:border-indigo-500"
              placeholder="e.g. Nightly Production Group"
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-bold uppercase tracking-wider text-slate-400 mb-1.5">
                {t('interval')}
              </label>
              <select
                value={interval}
                onChange={(e) => setIntervalVal(e.target.value)}
                className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-slate-100 focus:outline-none focus:border-indigo-500"
              >
                <option value="weekly">{t('weekly')}</option>
                <option value="monthly">{t('monthly')}</option>
                <option value="quarterly">{t('quarterly')}</option>
                <option value="yearly">{t('yearly')}</option>
              </select>
            </div>

            {interval !== 'weekly' && (
              <div>
                <label className="block text-xs font-bold uppercase tracking-wider text-slate-400 mb-1.5">
                  {t('targetWeek')}
                </label>
                <select
                  value={targetWeek}
                  onChange={(e) => setTargetWeek(Number(e.target.value))}
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-slate-100 focus:outline-none focus:border-indigo-500"
                >
                  <option value={1}>Week 1</option>
                  <option value={2}>Week 2</option>
                  <option value={3}>Week 3</option>
                  <option value={4}>Week 4</option>
                </select>
              </div>
            )}
          </div>

          <div>
            <div className="flex justify-between items-center mb-1.5 min-h-[16px]">
              <label className="block text-xs font-bold uppercase tracking-wider text-slate-400">
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
                  className="rounded border-slate-800 bg-slate-950 text-indigo-600 focus:ring-indigo-500 h-3.5 w-3.5 cursor-pointer"
                />
                <label htmlFor="useLocalTimeGroup" className="text-[10px] font-bold text-slate-500 hover:text-slate-400 transition-colors uppercase tracking-wider cursor-pointer select-none">
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
              <label className="block text-xs font-bold uppercase tracking-wider text-slate-400 mb-1.5">
                {t('startTime')}
              </label>
              <input
                type="time"
                required
                value={startTime}
                onChange={(e) => setStartTime(e.target.value)}
                className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-slate-100 focus:outline-none focus:border-indigo-500"
              />
            </div>

            <div>
              <label className="block text-xs font-bold uppercase tracking-wider text-slate-400 mb-1.5">
                {t('endTime')}
              </label>
              <input
                type="time"
                required
                value={endTime}
                onChange={(e) => setEndTime(e.target.value)}
                className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-slate-100 focus:outline-none focus:border-indigo-500"
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-bold uppercase tracking-wider text-slate-400 mb-1.5">
              {t('concurrencyLimit')}
            </label>
            <input
              type="number"
              required
              min={1}
              max={20}
              value={concurrencyLimit}
              onChange={(e) => setConcurrencyLimit(Number(e.target.value))}
              className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-slate-100 focus:outline-none focus:border-indigo-500 font-mono"
            />
          </div>

          <div className="flex items-center gap-3 pt-2">
            <input
              type="checkbox"
              id="randomize_days"
              checked={randomizeDays}
              onChange={(e) => setRandomizeDays(e.target.checked)}
              className="h-4.5 w-4.5 text-indigo-600 focus:ring-indigo-500 border-slate-800 rounded bg-slate-950"
            />
            <label htmlFor="randomize_days" className="text-sm font-medium text-slate-350 cursor-pointer">
              {t('randomizeDays')}
            </label>
          </div>

          <div className="flex justify-end gap-3 pt-4 border-t border-slate-800">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg font-medium transition duration-150"
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
    </div>
  );
}
