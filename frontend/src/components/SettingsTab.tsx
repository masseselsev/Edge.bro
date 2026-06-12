import React, { useState, useEffect } from 'react';
import { Save, Settings as Gear, CheckCircle } from 'lucide-react';
import { SearchableSelect } from './SearchableSelect';
import type { Option } from './SearchableSelect';

interface SettingsTabProps {
  onSettingsUpdated?: (settings: any) => void;
}

export default function SettingsTab({ onSettingsUpdated }: SettingsTabProps) {
  const [sshPort, setSshPort] = useState(12345);
  const [repoPath, setRepoPath] = useState('/data/borg');
  const [keepDaily, setKeepDaily] = useState(7);
  const [keepWeekly, setKeepWeekly] = useState(4);
  const [keepMonthly, setKeepMonthly] = useState(6);
  const [globalExclusions, setGlobalExclusions] = useState('/dev/*,/proc/*,/sys/*,/run/*,/mnt/*');
  const [orchestratorIp, setOrchestratorIp] = useState('');
  const [availableIps, setAvailableIps] = useState<string[]>([]);
  
  const [useLocalTime, setUseLocalTime] = useState(true);
  const [timezone, setTimezone] = useState(() => {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || 'Europe/Moscow';
    } catch (e) {
      return 'Europe/Moscow';
    }
  });

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [success, setSuccess] = useState(false);

  // Generate options
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
    fetch('/api/settings')
      .then(res => res.json())
      .then(data => {
        setSshPort(data.borg_ssh_port);
        setRepoPath(data.borg_repo_path);
        setKeepDaily(data.keep_daily);
        setKeepWeekly(data.keep_weekly);
        setKeepMonthly(data.keep_monthly);
        setGlobalExclusions(data.global_exclusions);
        setOrchestratorIp(data.orchestrator_ip || '');
        setAvailableIps(data.available_ips || []);
        
        const dbTz = data.timezone || 'Browser Local';
        let resolvedTz = 'Europe/Moscow';
        try {
          resolvedTz = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Europe/Moscow';
        } catch (e) {}

        if (dbTz === 'Browser Local') {
          setUseLocalTime(true);
          setTimezone(resolvedTz);
        } else {
          setUseLocalTime(false);
          setTimezone(dbTz);
        }
        setLoading(false);
      })
      .catch(e => {
        console.error(e);
        setLoading(false);
      });
  }, []);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setSuccess(false);
    try {
      const savedTz = useLocalTime ? 'Browser Local' : timezone;
      const res = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          borg_ssh_port: sshPort,
          borg_repo_path: repoPath,
          keep_daily: keepDaily,
          keep_weekly: keepWeekly,
          keep_monthly: keepMonthly,
          global_exclusions: globalExclusions,
          orchestrator_ip: orchestratorIp,
          timezone: savedTz
        })
      });
      if (res.ok) {
        const data = await res.json();
        setSuccess(true);
        setAvailableIps(data.available_ips || []);
        if (onSettingsUpdated) {
          onSettingsUpdated(data);
        }
        setTimeout(() => setSuccess(false), 3000);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div className="text-zinc-500 text-center py-8">Loading configuration settings...</div>;
  }

  return (
    <div className="max-w-2xl mx-auto p-6 bg-zinc-900 border border-zinc-800 rounded-2xl space-y-6">
      <div className="flex justify-between items-center border-b border-zinc-800 pb-4">
        <div>
          <h3 className="text-lg font-bold text-white flex items-center gap-2"><Gear size={18} /> Orchestrator Settings</h3>
          <p className="text-xs text-zinc-400">Configure global parameters and Borg pruning rules.</p>
        </div>
      </div>

      <form onSubmit={handleSave} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-semibold text-zinc-400 mb-1.5">Borg SSH Daemon Port</label>
            <input
              type="number"
              required
              value={sshPort}
              onChange={(e) => setSshPort(parseInt(e.target.value))}
              className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-white text-sm focus:border-indigo-500 focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-xs font-semibold text-zinc-400 mb-1.5">Repository Location</label>
            <input
              type="text"
              required
              value={repoPath}
              onChange={(e) => setRepoPath(e.target.value)}
              className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-white text-sm focus:border-indigo-500 focus:outline-none"
            />
          </div>
        </div>

        <div>
          <h4 className="text-xs font-bold text-white uppercase tracking-wider mt-4 mb-2">Global Pruning Retention Policies</h4>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="block text-[10px] font-semibold text-zinc-400 mb-1">Keep Daily</label>
              <input
                type="number"
                required
                value={keepDaily}
                onChange={(e) => setKeepDaily(parseInt(e.target.value))}
                className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-white text-sm focus:border-indigo-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-[10px] font-semibold text-zinc-400 mb-1">Keep Weekly</label>
              <input
                type="number"
                required
                value={keepWeekly}
                onChange={(e) => setKeepWeekly(parseInt(e.target.value))}
                className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-white text-sm focus:border-indigo-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-[10px] font-semibold text-zinc-400 mb-1">Keep Monthly</label>
              <input
                type="number"
                required
                value={keepMonthly}
                onChange={(e) => setKeepMonthly(parseInt(e.target.value))}
                className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-white text-sm focus:border-indigo-500 focus:outline-none"
              />
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-semibold text-zinc-400 mb-1.5">Orchestrator IP Address (for nodes connection)</label>
            <input
              type="text"
              list="settings-orchestrator-ips"
              value={orchestratorIp}
              onChange={(e) => setOrchestratorIp(e.target.value)}
              placeholder="e.g. 192.168.222.2 (leave blank to auto-detect)"
              className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-white text-sm focus:border-indigo-500 focus:outline-none"
            />
            <datalist id="settings-orchestrator-ips">
              {availableIps.map(ip => <option key={ip} value={ip} />)}
            </datalist>
          </div>
          <div>
            <div className="flex justify-between items-center mb-1.5 h-[16px]">
              <label className="block text-xs font-semibold text-zinc-400">System Timezone</label>
              <div className="flex items-center gap-1.5">
                <input
                  type="checkbox"
                  id="useLocalTime"
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
                <label htmlFor="useLocalTime" className="text-[10px] font-bold text-zinc-500 hover:text-zinc-400 transition-colors uppercase tracking-wider cursor-pointer select-none">
                  Use Browser Local
                </label>
              </div>
            </div>
            <SearchableSelect
              options={timezoneOptions}
              value={timezone}
              onChange={setTimezone}
              disabled={useLocalTime}
              placeholder="Select Timezone..."
            />
          </div>
        </div>

        <div>
          <label className="block text-xs font-semibold text-zinc-400 mb-1.5">Global File Exclusion Paths (comma-separated)</label>
          <textarea
            rows={3}
            value={globalExclusions}
            onChange={(e) => setGlobalExclusions(e.target.value)}
            className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-white text-sm font-mono focus:border-indigo-500 focus:outline-none"
          />
        </div>

        <div className="border-t border-zinc-800 pt-4 flex items-center justify-between">
          {success && (
            <span className="text-emerald-400 text-xs flex items-center gap-1.5">
              <CheckCircle size={14} /> Configuration saved successfully.
            </span>
          )}
          {!success && <div />}
          <button
            type="submit"
            disabled={saving}
            className="flex items-center gap-2 px-5 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg font-bold text-sm tracking-wide shadow transition-colors disabled:opacity-50"
          >
            <Save size={16} /> {saving ? 'Saving...' : 'Save Settings'}
          </button>
        </div>
      </form>
    </div>
  );
}
