import React, { useState, useEffect } from 'react';
import { Save, Settings as Gear, CheckCircle } from 'lucide-react';
import { SearchableSelect, DropdownTextInput } from './SearchableSelect';
import type { Option } from './SearchableSelect';
import { useTranslation } from '../context/TranslationContext';
import type { Language } from '../i18n/translations';
import AdminsTab from './AdminsTab';

interface SettingsTabProps {
  onSettingsUpdated?: (settings: any) => void;
  currentUser?: any;
}

export default function SettingsTab({ onSettingsUpdated, currentUser }: SettingsTabProps) {
  const { t, setLanguage } = useTranslation();
  const [activeSubTab, setActiveSubTab] = useState<'general' | 'admins'>('general');
  const [sshPort, setSshPort] = useState(12345);
  const [repoPath, setRepoPath] = useState('/data/borg');
  const [policyType, setPolicyType] = useState<'interval' | 'count' | 'timeframe'>('interval');
  const [policyKeepDaily, setPolicyKeepDaily] = useState(7);
  const [policyKeepWeekly, setPolicyKeepWeekly] = useState(4);
  const [policyKeepMonthly, setPolicyKeepMonthly] = useState(6);
  const [policyKeepLast, setPolicyKeepLast] = useState(5);
  const [policyWithinValue, setPolicyWithinValue] = useState(3);
  const [policyWithinUnit, setPolicyWithinUnit] = useState<'d' | 'w' | 'm' | 'y'>('m');
  const [globalExclusions, setGlobalExclusions] = useState('/dev/*,/proc/*,/sys/*,/run/*,/mnt/*');
  const [orchestratorIp, setOrchestratorIp] = useState('');
  const [availableIps, setAvailableIps] = useState<string[]>([]);
  const [language, setLanguageState] = useState<Language>('en');
  const [defaultCompression, setDefaultCompression] = useState('zstd:3');
  const [defaultCpuQuota, setDefaultCpuQuota] = useState<number | ''>('');
  const [hostDataPath, setHostDataPath] = useState<string | null>(null);
  const [serverIpsInput, setServerIpsInput] = useState('');
  const [maxKioskIsos, setMaxKioskIsos] = useState(5);
  
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

  const compressionOptions = React.useMemo(() => [
    { value: 'none', label: 'none' },
    { value: 'lz4', label: 'lz4' },
    { value: 'zstd:1', label: 'zstd:1' },
    { value: 'zstd:3', label: 'zstd:3' },
    { value: 'zstd:5', label: 'zstd:5' },
    { value: 'zstd:9', label: 'zstd:9' }
  ], []);

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

  useEffect(() => {
    fetch('/api/settings')
      .then(res => res.json())
      .then(data => {
        setSshPort(data.borg_ssh_port);
        setRepoPath(data.borg_repo_path);
        
        const rp = data.retention_policy;
        if (rp) {
          setPolicyType(rp.type || 'interval');
          setPolicyKeepDaily(rp.keep_daily ?? 7);
          setPolicyKeepWeekly(rp.keep_weekly ?? 4);
          setPolicyKeepMonthly(rp.keep_monthly ?? 6);
          setPolicyKeepLast(rp.keep_last ?? 5);
          setPolicyWithinValue(rp.within_value ?? 3);
          setPolicyWithinUnit(rp.within_unit || 'm');
        } else {
          setPolicyType('interval');
          setPolicyKeepDaily(data.keep_daily ?? 7);
          setPolicyKeepWeekly(data.keep_weekly ?? 4);
          setPolicyKeepMonthly(data.keep_monthly ?? 6);
        }

        setGlobalExclusions(data.global_exclusions);
        setOrchestratorIp(data.orchestrator_ip || '');
        setAvailableIps(data.available_ips || []);
        setLanguageState(data.language || 'en');
        setDefaultCompression(data.default_compression || 'zstd:3');
        setDefaultCpuQuota(data.default_cpu_quota ?? '');
        if (data.borg_host_data_path) {
          setHostDataPath(data.borg_host_data_path);
        }
        setServerIpsInput(data.server_ips ? data.server_ips.join(', ') : '');
        if (data.max_kiosk_isos !== undefined) {
          setMaxKioskIsos(data.max_kiosk_isos);
        }
        
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
          keep_daily: policyKeepDaily,
          keep_weekly: policyKeepWeekly,
          keep_monthly: policyKeepMonthly,
          global_exclusions: globalExclusions,
          orchestrator_ip: orchestratorIp,
          timezone: savedTz,
          language: language,
          default_compression: defaultCompression,
          default_cpu_quota: defaultCpuQuota === '' ? null : Number(defaultCpuQuota),
          server_ips: serverIpsInput.split(',').map(s => s.trim()).filter(Boolean),
          max_kiosk_isos: maxKioskIsos,
          retention_policy: {
            type: policyType,
            keep_daily: policyKeepDaily,
            keep_weekly: policyKeepWeekly,
            keep_monthly: policyKeepMonthly,
            keep_last: policyKeepLast,
            within_value: policyWithinValue,
            within_unit: policyWithinUnit
          }
        })
      });
      if (res.ok) {
        const data = await res.json();
        setSuccess(true);
        setAvailableIps(data.available_ips || []);
        setLanguage(data.language);
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
    return <div className="text-zinc-500 text-center py-8">{t('saving')}</div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-zinc-50 flex items-center gap-2">
            <Gear size={24} className="text-indigo-400" />
            {t('orchestratorSettings')}
          </h2>
          <p className="text-sm text-zinc-400 mt-1">{t('orchestratorSettingsSub')}</p>
        </div>
      </div>

      {(currentUser?.is_superadmin || currentUser?.is_admin_plus) && (
        <div className="flex border-b border-zinc-800 gap-4 text-xs font-bold pb-px mb-2">
          <button
            type="button"
            onClick={() => setActiveSubTab('general')}
            className={`pb-2 border-b-2 px-1 transition-all cursor-pointer outline-none ${
              activeSubTab === 'general'
                ? 'border-indigo-500 text-zinc-150'
                : 'border-transparent text-zinc-450 hover:text-zinc-300'
            }`}
          >
            {t('orchestratorSettings')}
          </button>
          <button
            type="button"
            onClick={() => setActiveSubTab('admins')}
            className={`pb-2 border-b-2 px-1 transition-all cursor-pointer outline-none ${
              activeSubTab === 'admins'
                ? 'border-indigo-500 text-zinc-150'
                : 'border-transparent text-zinc-450 hover:text-zinc-300'
            }`}
          >
            {t('tabAdmins')}
          </button>
        </div>
      )}

      {activeSubTab === 'admins' && (currentUser?.is_superadmin || currentUser?.is_admin_plus) ? (
        <AdminsTab currentUser={currentUser} />
      ) : (
        <form onSubmit={handleSave} className="space-y-6">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
            {/* Left Column: Connection & General Settings */}
            <div className="p-6 bg-zinc-900 border border-zinc-800 rounded-2xl space-y-4 shadow-xl">
              <h3 className="text-sm font-bold text-zinc-50 border-b border-zinc-850 pb-2">
                {language === 'ru' ? 'Общие настройки' : language === 'uk' ? 'Загальні налаштування' : 'General & Connection'}
              </h3>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('borgSshPort')}</label>
                  <input
                    type="number"
                    required
                    value={sshPort}
                    onChange={(e) => setSshPort(parseInt(e.target.value))}
                    className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:border-indigo-500 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('repoLocation')}</label>
                  <input
                    type="text"
                    required
                    value={repoPath}
                    onChange={(e) => setRepoPath(e.target.value)}
                    className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:border-indigo-500 focus:outline-none"
                  />
                  {hostDataPath && (
                    <p className="text-[10px] text-zinc-500 mt-1 leading-relaxed">
                      {language === 'ru' 
                        ? `Физический путь на хосте (настраивается в .env): ${hostDataPath}`
                        : language === 'uk'
                        ? `Фізичний шлях на хості (налаштовується в .env): ${hostDataPath}`
                        : `Physical host path (configured in .env): ${hostDataPath}`}
                    </p>
                  )}
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('compressionMode')}</label>
                  <SearchableSelect
                    options={compressionOptions}
                    value={defaultCompression}
                    onChange={setDefaultCompression}
                    placeholder={t('selectCompressionPlaceholder')}
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('cpuQuota')}</label>
                  <input
                    type="number"
                    min={0}
                    max={400}
                    value={defaultCpuQuota}
                    onChange={(e) => setDefaultCpuQuota(e.target.value === '' ? '' : Number(e.target.value))}
                    className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:border-indigo-500 focus:outline-none font-mono"
                    placeholder="e.g. 50"
                  />
                  <p className="text-[10px] text-zinc-500 mt-1 leading-relaxed">
                    {t('cpuQuotaHint')}
                  </p>
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('orchestratorIpLabelSettings')}</label>
                  <DropdownTextInput
                    value={orchestratorIp}
                    onChange={setOrchestratorIp}
                    options={availableIps}
                    placeholder={t('orchestratorIpPlaceholderSettings')}
                  />
                </div>
                <div>
                  <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-1 mb-1.5 min-h-[16px]">
                    <label className="block text-xs font-semibold text-zinc-400">{t('systemTimezone')}</label>
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
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
                <div>
                  <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('serverIpsLabel') || 'Server IP Addresses'}</label>
                  <input
                    type="text"
                    value={serverIpsInput}
                    onChange={(e) => setServerIpsInput(e.target.value)}
                    placeholder={t('serverIpsPlaceholder') || 'e.g. 192.168.1.100, 10.0.0.5'}
                    className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:border-indigo-500 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-zinc-400 mb-1.5">
                    {language === 'ru' 
                      ? 'Макс. число Live-ISO в истории' 
                      : language === 'uk' 
                      ? 'Макс. число Live-ISO в історії' 
                      : 'Max Kiosk ISOs in Repository'}
                  </label>
                  <input
                    type="number"
                    min={1}
                    required
                    value={maxKioskIsos}
                    onChange={(e) => setMaxKioskIsos(parseInt(e.target.value) || 5)}
                    className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:border-indigo-500 focus:outline-none"
                  />
                </div>
              </div>

              <div>
                <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('globalExclusionsLabel')}</label>
                <textarea
                  rows={3}
                  value={globalExclusions}
                  onChange={(e) => setGlobalExclusions(e.target.value)}
                  className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm font-mono focus:border-indigo-500 focus:outline-none"
                />
              </div>
            </div>

            {/* Right Column: Global Pruning (Retention Policies) */}
            <div className="p-6 bg-zinc-900 border border-zinc-800 rounded-2xl space-y-4 shadow-xl">
              <h3 className="text-sm font-bold text-zinc-50 border-b border-zinc-850 pb-2">
                {t('globalPruning')}
              </h3>
              <p className="text-xs text-zinc-400 leading-relaxed">
                {language === 'ru' ? 'Настройка правил автоматического удаления старых бэкапов в архиве.' : language === 'uk' ? 'Налаштування правил автоматичного видалення старих бекапів в архіві.' : 'Configure rules for automatic deletion of older snapshots in the archive.'}
              </p>

              <div className="space-y-4">
                <div>
                  <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('retentionType')}</label>
                  <SearchableSelect
                    options={policyTypeOptions}
                    value={policyType}
                    onChange={setPolicyType}
                    placeholder={t('selectPolicyTypePlaceholder')}
                  />
                </div>

                {policyType === 'interval' && (
                  <div className="grid grid-cols-3 gap-3 animate-fade-in">
                    <div>
                      <label className="block text-[10px] font-semibold text-zinc-400 mb-1">{t('keepDaily')}</label>
                      <input
                        type="number"
                        required
                        min={0}
                        value={policyKeepDaily}
                        onChange={(e) => setPolicyKeepDaily(parseInt(e.target.value) || 0)}
                        className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:border-indigo-500 focus:outline-none"
                      />
                    </div>
                    <div>
                      <label className="block text-[10px] font-semibold text-zinc-400 mb-1">{t('keepWeekly')}</label>
                      <input
                        type="number"
                        required
                        min={0}
                        value={policyKeepWeekly}
                        onChange={(e) => setPolicyKeepWeekly(parseInt(e.target.value) || 0)}
                        className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:border-indigo-500 focus:outline-none"
                      />
                    </div>
                    <div>
                      <label className="block text-[10px] font-semibold text-zinc-400 mb-1">{t('keepMonthly')}</label>
                      <input
                        type="number"
                        required
                        min={0}
                        value={policyKeepMonthly}
                        onChange={(e) => setPolicyKeepMonthly(parseInt(e.target.value) || 0)}
                        className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:border-indigo-500 focus:outline-none"
                      />
                    </div>
                  </div>
                )}

                {policyType === 'count' && (
                  <div className="animate-fade-in">
                    <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('keepLastLabel')}</label>
                    <input
                      type="number"
                      required
                      min={1}
                      value={policyKeepLast}
                      onChange={(e) => setPolicyKeepLast(parseInt(e.target.value) || 1)}
                      className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:border-indigo-500 focus:outline-none"
                    />
                  </div>
                )}

                {policyType === 'timeframe' && (
                  <div className="grid grid-cols-3 gap-3 animate-fade-in">
                    <div className="col-span-2">
                      <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('keepWithinLabel')}</label>
                      <input
                        type="number"
                        required
                        min={1}
                        value={policyWithinValue}
                        onChange={(e) => setPolicyWithinValue(parseInt(e.target.value) || 1)}
                        className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:border-indigo-500 focus:outline-none"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-semibold text-zinc-400 mb-1.5">&nbsp;</label>
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
            </div>
          </div>

          <div className="border-t border-zinc-800 pt-4 flex items-center justify-between">
            {success && (
              <span className="text-emerald-400 text-xs flex items-center gap-1.5">
                <CheckCircle size={14} /> {t('settingsSuccess')}
              </span>
            )}
            {!success && <div />}
            <button
              type="submit"
              disabled={saving}
              className="flex items-center gap-2 px-5 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg font-bold text-sm tracking-wide shadow transition-colors disabled:opacity-50"
            >
              <Save size={16} /> {saving ? t('saving') : t('saveSettings')}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
