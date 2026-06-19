import React, { useState, useEffect, useRef } from 'react';
import { Server, HardDrive, History, Settings as Gear, Terminal, Cpu, Globe2, Wifi, LogOut, Calendar, Sun, Moon, Link2, Copy, ShieldAlert, RefreshCw } from 'lucide-react';
import FleetTab from './components/FleetTab';
import FlasherTab from './components/FlasherTab';
import HistoryTab from './components/HistoryTab';
import LogsTab from './components/LogsTab';
import SettingsTab from './components/SettingsTab';
import ClientIsoTab from './components/ClientIsoTab';
import ScheduleTab from './components/ScheduleTab';
import TaskLogsModal from './components/TaskLogsModal';
import NetworkSettingsModal from './components/NetworkSettingsModal';
import { DropdownTextInput } from './components/SearchableSelect';
import { TranslationProvider, useTranslation } from './context/TranslationContext';
import type { Language } from './i18n/translations';

type Tab = 'fleet' | 'flasher' | 'history' | 'logs' | 'settings' | 'clientiso' | 'schedule';

function LanguageSelector() {
  const { language, setLanguage } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleSelect = async (lang: Language) => {
    setLanguage(lang);
    setIsOpen(false);
    
    // Save selected language to settings database
    try {
      const getRes = await fetch('/api/settings');
      if (getRes.ok) {
        const settings = await getRes.json();
        await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            ...settings,
            language: lang
          })
        });
      }
    } catch (err) {
      console.error('Failed to sync language selection to settings backend:', err);
    }
  };

  const flags: Record<Language, React.ReactNode> = {
    en: (
      <svg className="w-5 h-3.5 rounded-sm shadow-sm inline-block" viewBox="0 0 60 30" style={{verticalAlign: 'middle'}}>
        <rect width="60" height="30" fill="#012169"/>
        <path d="M0,0 L60,30 M60,0 L0,30" stroke="#fff" strokeWidth="6"/>
        <path d="M0,0 L60,30 M60,0 L0,30" stroke="#C8102E" strokeWidth="4"/>
        <path d="M30,0 V30 M0,15 H60" stroke="#fff" strokeWidth="10"/>
        <path d="M30,0 V30 M0,15 H60" stroke="#C8102E" strokeWidth="6"/>
      </svg>
    ),
    ru: (
      <svg className="w-5 h-3.5 rounded-sm shadow-sm inline-block" viewBox="0 0 9 6" style={{verticalAlign: 'middle'}}>
        <rect width="9" height="2" fill="#fff"/>
        <rect y="2" width="9" height="2" fill="#0039A6"/>
        <rect y="4" width="9" height="2" fill="#D52B1E"/>
      </svg>
    ),
    uk: (
      <svg className="w-5 h-3.5 rounded-sm shadow-sm inline-block" viewBox="0 0 3 2" style={{verticalAlign: 'middle'}}>
        <rect width="3" height="1" fill="#0057B7"/>
        <rect y="1" width="3" height="1" fill="#FFD700"/>
      </svg>
    )
  };

  const labels: Record<Language, string> = {
    en: 'English (EN)',
    ru: 'Русский (RU)',
    uk: 'Українська (UA)'
  };

  return (
    <div className="relative" ref={containerRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-zinc-950 hover:bg-zinc-900 border border-zinc-800 text-xs text-zinc-300 font-bold transition-all duration-200 cursor-pointer outline-none"
      >
        <span className="text-sm leading-none">{flags[language]}</span>
        <span>{labels[language] || language.toUpperCase()}</span>
        <svg className={`w-3 h-3 text-zinc-500 transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="3">
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {isOpen && (
        <div className="absolute right-0 mt-1.5 w-44 rounded-lg bg-zinc-900 border border-zinc-800 shadow-2xl p-1 z-50 origin-top-right animate-dropdown-in">
          {(['en', 'uk', 'ru'] as Language[]).map((lang) => (
            <button
              key={lang}
              onClick={() => handleSelect(lang)}
              className={`w-full text-left px-3 py-2 text-xs font-semibold rounded-md transition-colors flex items-center justify-between ${
                language === lang
                  ? 'bg-indigo-50 dark:bg-indigo-600/20 text-indigo-800 dark:text-indigo-300 border border-indigo-100 dark:border-indigo-500/20'
                  : 'text-zinc-400 hover:text-zinc-50 hover:bg-zinc-800'
              }`}
            >
              <div className="flex items-center gap-2">
                <span className="text-sm leading-none">{flags[lang]}</span>
                <span>{labels[lang]}</span>
              </div>
              {language === lang && <span className="text-[10px] text-indigo-450">✓</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function AppContent() {
  const { t, language } = useTranslation();
  const [activeTab, setActiveTab] = useState<Tab>('fleet');
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    const saved = localStorage.getItem('theme');
    return (saved === 'light' || saved === 'dark') ? saved : 'dark';
  });

  useEffect(() => {
    if (theme === 'light') {
      document.documentElement.classList.add('light');
    } else {
      document.documentElement.classList.remove('light');
    }
    localStorage.setItem('theme', theme);
  }, [theme]);

  const [showNetworkModal, setShowNetworkModal] = useState(false);
  const [networkStatus, setNetworkStatus] = useState<any>(null);
  const [logTaskId, setLogTaskId] = useState<string | null>(null);
  const [logTaskTitle, setLogTaskTitle] = useState<string>('');
  
  const [showIpPromptModal, setShowIpPromptModal] = useState(false);
  const [orchestratorIp, setOrchestratorIp] = useState('');
  const [settings, setSettings] = useState<any>(null);
  const [availableIps, setAvailableIps] = useState<string[]>([]);
  const [savingIp, setSavingIp] = useState(false);
  const [appVersion, setAppVersion] = useState('');
  const [isKiosk, setIsKiosk] = useState(false);
  const [restoreMode, setRestoreMode] = useState<'offline' | 'online'>('offline');
  const [kioskOrchestratorIp, setKioskOrchestratorIp] = useState('');
  const [connectionKeyphrase, setConnectionKeyphrase] = useState('');

  // Watchdog states
  const [watchdogStatus, setWatchdogStatus] = useState<{
    detected: boolean;
    port: string | null;
    seconds_left: number | null;
    frozen: boolean;
  } | null>(null);
  const [showWatchdogModal, setShowWatchdogModal] = useState(false);
  const [hasShownWatchdogModal, setHasShownWatchdogModal] = useState(false);
  const [watchdogActionLoading, setWatchdogActionLoading] = useState(false);

  // Pairing states
  const [kioskUuid, setKioskUuid] = useState('');
  const [showPairingModal, setShowPairingModal] = useState(false);
  const [pairingIp, setPairingIp] = useState('');
  const [pairingKey, setPairingKey] = useState('');
  const [pairingSubmitting, setPairingSubmitting] = useState(false);
  const [pairingError, setPairingError] = useState('');
  const [pairingSuccess, setPairingSuccess] = useState('');

  const handlePairingSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setPairingSubmitting(true);
    setPairingError('');
    setPairingSuccess('');
    try {
      const res = await fetch('/api/kiosk/connect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          orchestrator_ip: pairingIp.trim(),
          key: pairingKey.trim()
        })
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Connection handshake failed');
      }
      
      setPairingSuccess(t('kioskPairingSuccess') || 'Connected and paired successfully!');
      setKioskOrchestratorIp(pairingIp.trim());
      
      setTimeout(async () => {
        try {
          const vRes = await fetch('/api/version');
          if (vRes.ok) {
            const vData = await vRes.json();
            setConnectionKeyphrase(vData.auth_token || '');
          }
        } catch {}
        setShowPairingModal(false);
      }, 1500);
      
    } catch (err: any) {
      setPairingError(err.message);
    } finally {
      setPairingSubmitting(false);
    }
  };

  useEffect(() => {
    if (!isKiosk) return;
    fetch('/api/kiosk/mode')
      .then(res => res.json())
      .then(data => {
        if (data && data.mode) {
          setRestoreMode(data.mode);
        }
      })
      .catch(err => console.error(err));
  }, [isKiosk]);

  useEffect(() => {
    if (!isKiosk) return;

    const fetchNetStatus = async () => {
      try {
        const res = await fetch('/api/network/status');
        if (res.ok) {
          const data = await res.json();
          setNetworkStatus(data);
        }
      } catch (err) {
        console.error('Failed to fetch network status:', err);
      }
    };
    fetchNetStatus();
    const interval = setInterval(fetchNetStatus, 7000);
    return () => clearInterval(interval);
  }, [isKiosk]);

  useEffect(() => {
    if (!isKiosk) return;

    const fetchWatchdogStatus = async () => {
      try {
        const wdRes = await fetch('/api/kiosk/watchdog/status');
        if (wdRes.ok) {
          const data = await wdRes.json();
          setWatchdogStatus(data);
          if (data.detected && !data.frozen && !hasShownWatchdogModal) {
            setShowWatchdogModal(true);
            setHasShownWatchdogModal(true);
          }
        }
      } catch (e) {
        console.error('Failed to fetch watchdog status:', e);
      }
    };

    fetchWatchdogStatus();
    const wdtInterval = setInterval(fetchWatchdogStatus, 4000);
    return () => clearInterval(wdtInterval);
  }, [isKiosk, hasShownWatchdogModal]);

  const handleFreezeWatchdog = async () => {
    setWatchdogActionLoading(true);
    try {
      const res = await fetch('/api/kiosk/watchdog/freeze', { method: 'POST' });
      if (!res.ok) throw new Error("Failed to freeze watchdog");
      // Fetch status immediately to update UI
      const statusRes = await fetch('/api/kiosk/watchdog/status');
      if (statusRes.ok) {
        const data = await statusRes.json();
        setWatchdogStatus(data);
      }
      setShowWatchdogModal(false);
    } catch (err: any) {
      alert(err.message || "Error communication with watchdog controller");
    } finally {
      setWatchdogActionLoading(false);
    }
  };

  const handleUnfreezeWatchdog = async () => {
    setWatchdogActionLoading(true);
    try {
      const res = await fetch('/api/kiosk/watchdog/unfreeze', { method: 'POST' });
      if (!res.ok) throw new Error("Failed to unfreeze watchdog");
      const statusRes = await fetch('/api/kiosk/watchdog/status');
      if (statusRes.ok) {
        const data = await statusRes.json();
        setWatchdogStatus(data);
      }
    } catch (err: any) {
      alert(err.message || "Error communication with watchdog controller");
    } finally {
      setWatchdogActionLoading(false);
    }
  };

  useEffect(() => {
    // Fetch current app version from API with retries in case of startup delays
    let retryCount = 0;
    const fetchVersion = () => {
      fetch('/api/version')
        .then(res => {
          if (!res.ok) throw new Error('HTTP error ' + res.status);
          return res.json();
        })
        .then(data => {
          if (data && data.version) {
            setAppVersion(data.version);
          }
          if (data && data.is_kiosk) {
            setIsKiosk(true);
            setActiveTab('flasher');
            setKioskOrchestratorIp(data.orchestrator_ip || '');
            setConnectionKeyphrase(data.auth_token || '');
            setKioskUuid(data.kiosk_uuid || '');
          }
        })
        .catch(err => {
          console.error('Error fetching version:', err);
          if (retryCount < 5) {
            retryCount++;
            setTimeout(fetchVersion, 3000);
          }
        });
    };
    fetchVersion();

    // Fetch current settings on mount
    fetch('/api/settings')
      .then(res => res.json())
      .then(sett => {
        setSettings(sett);
        setOrchestratorIp(sett.orchestrator_ip || '');
        setAvailableIps(sett.available_ips || []);
        
        // Check if nodes list is empty on mount
        fetch('/api/nodes')
          .then(res => res.json())
          .then(nodes => {
            if (nodes.length === 0) {
              setShowIpPromptModal(true);
            }
          })
          .catch(err => console.error(err));
      })
      .catch(err => console.error(err));
  }, []);

  const handleExitKiosk = async () => {
    if (window.confirm(t('exitKioskConfirm'))) {
      try {
        await fetch('/api/kiosk/exit', { method: 'POST' });
      } catch (err) {
        console.error("Failed to trigger kiosk exit:", err);
      }
    }
  };

  const handleToggleMode = async () => {
    const nextMode = restoreMode === 'offline' ? 'online' : 'offline';
    try {
      const res = await fetch('/api/kiosk/mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: nextMode })
      });
      if (res.ok) {
        setRestoreMode(nextMode);
        window.location.reload();
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleSaveIp = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!settings) return;
    setSavingIp(true);
    try {
      const res = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...settings,
          orchestrator_ip: orchestratorIp
        })
      });
      if (res.ok) {
        setShowIpPromptModal(false);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setSavingIp(false);
    }
  };

  const handleViewLogs = (taskId: string, title: string) => {
    setLogTaskId(taskId);
    setLogTaskTitle(title);
  };

  const renderTabContent = () => {
    const tz = settings?.timezone || 'Browser Local';
    switch (activeTab) {
      case 'flasher':
        return <FlasherTab onViewLogs={handleViewLogs} timezone={tz} restoreMode={restoreMode} isKiosk={isKiosk} />;
      case 'clientiso':
        return <ClientIsoTab onViewLogs={handleViewLogs} />;
      case 'history':
        return <HistoryTab onViewLogs={handleViewLogs} timezone={tz} />;
      case 'logs':
        return <LogsTab onViewLogs={handleViewLogs} timezone={tz} isKiosk={isKiosk} />;
      case 'settings':
        return <SettingsTab onSettingsUpdated={setSettings} />;
      case 'schedule':
        return <ScheduleTab />;
      case 'fleet':
      default:
        return <FleetTab onViewLogs={handleViewLogs} timezone={tz} />;
    }
  };

  return (
    <div className="min-h-full flex flex-col font-sans select-none">
      {/* Global Header */}
      <header className="bg-zinc-900/80 backdrop-blur-md border-b border-zinc-800/80 sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-6 py-3 space-y-3">
          {/* Row 1: Logo/Title + Quick Actions / Language Selector */}
          <div className="flex flex-col sm:flex-row items-center justify-between gap-4">
            {/* Left: Brand Identity with SVG logo */}
            <div className="flex items-center gap-3 flex-shrink-0">
              <div className="relative p-2 bg-indigo-600/15 border border-indigo-500/30 rounded-lg shadow-lg flex items-center justify-center w-9 h-9">
                <svg className="w-5 h-5 text-indigo-400 filter drop-shadow-[0_0_4px_rgba(99,102,241,0.6)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                </svg>
                <span className="absolute top-1.5 right-1.5 w-1.5 h-1.5 bg-emerald-500 rounded-full animate-ping"></span>
                <span className="absolute top-1.5 right-1.5 w-1.5 h-1.5 bg-emerald-500 rounded-full"></span>
              </div>
              <div>
                <h1 className="text-base font-bold text-zinc-50 tracking-tight leading-none flex items-center gap-2">
                  <span className="bg-indigo-500/10 text-indigo-300 border border-indigo-500/20 px-2 py-0.5 rounded font-mono font-bold text-xs uppercase tracking-wider">Edge B.R.O.</span>
                  <span className="text-[10px] bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 px-1.5 py-0.5 rounded font-mono font-bold">{appVersion}</span>
                </h1>
                <p className="text-[9px] text-zinc-500 font-semibold mt-1.5 uppercase tracking-wider">
                  {language === 'ru' ? 'Оркестратор бэкапа и восстановления Edge' : language === 'uk' ? 'Оркестратор бекапу та відновлення Edge' : 'Edge Backup & Restore Orchestrator'}
                </p>
              </div>
            </div>

            {/* Right: Actions + Custom Language Switcher Dropdown */}
            <div className="flex flex-wrap items-center justify-center gap-3 flex-shrink-0">
              {isKiosk && (
                <>
                  <button
                    onClick={handleToggleMode}
                    className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-zinc-950 hover:bg-zinc-900 border border-zinc-800 text-xs text-zinc-300 font-bold transition-all duration-200 cursor-pointer"
                    title="Toggle restoration mode"
                  >
                    {restoreMode === 'online' ? (
                      <>
                        <Globe2 size={13} className="text-indigo-400" />
                        <span>{t('modeOnline')}</span>
                      </>
                    ) : (
                      <>
                        <HardDrive size={13} className="text-amber-400" />
                        <span>{t('modeOffline')}</span>
                      </>
                    )}
                  </button>
                  {restoreMode === 'online' && (
                    <button
                      onClick={() => {
                        setPairingIp(kioskOrchestratorIp || window.location.hostname);
                        setPairingKey('');
                        setPairingError('');
                        setPairingSuccess('');
                        setShowPairingModal(true);
                      }}
                      className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-indigo-950/40 hover:bg-indigo-950/60 border border-indigo-900/30 hover:border-indigo-900/60 text-xs text-indigo-400 font-bold transition-all duration-200 cursor-pointer animate-fade-in"
                      title="Link to Orchestrator Server"
                    >
                      <Link2 size={13} className="text-indigo-400 animate-pulse" />
                      <span>{t('linkServerButton') || 'Pair Server'}</span>
                    </button>
                  )}
                  <button
                    onClick={() => setShowNetworkModal(true)}
                    className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-zinc-950 hover:bg-zinc-900 border border-zinc-800 text-xs text-zinc-300 font-bold transition-all duration-200 cursor-pointer"
                  >
                    {networkStatus?.wired?.connected ? (
                      <>
                        <Globe2 size={13} className="text-emerald-400" />
                        <span>{t('wiredLink')}</span>
                      </>
                    ) : networkStatus?.wifi?.connected ? (
                      <>
                        <Wifi size={13} className="text-emerald-400" />
                        <span>{networkStatus.wifi.ssid}</span>
                      </>
                    ) : (
                      <>
                        <Globe2 size={13} className="text-rose-400" />
                        <span className="text-rose-400 font-bold">{t('offline')}</span>
                      </>
                    )}
                  </button>
                  <button
                    onClick={handleExitKiosk}
                    className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-red-950/20 hover:bg-red-950/40 border border-red-900/30 hover:border-red-900/60 text-xs text-red-400 font-bold transition-all duration-200 cursor-pointer"
                    title="Exit Kiosk Mode"
                  >
                    <LogOut size={13} />
                    <span>{t('exitKiosk')}</span>
                  </button>
                </>
              )}

              {/* Language Dropdown Selector */}
              <div className="flex items-center gap-2">
                <LanguageSelector />
                <button
                  onClick={() => setTheme(prev => prev === 'dark' ? 'light' : 'dark')}
                  className="p-1.5 bg-zinc-950 hover:bg-zinc-900 border border-zinc-800 rounded-lg text-zinc-400 hover:text-zinc-200 transition-all cursor-pointer flex items-center justify-center"
                  title={theme === 'dark' ? t('switchToLightMode') : t('switchToDarkMode')}
                >
                  {theme === 'dark' ? <Sun size={14} /> : <Moon size={14} />}
                </button>
              </div>
            </div>
          </div>

          {/* Row 2: Tab Navigation Buttons */}
          <div className="border-t border-zinc-800/60 pt-2 flex justify-center w-full">
            <nav className="w-full flex flex-wrap items-center justify-center gap-1 bg-zinc-950 p-1 rounded-xl border border-zinc-800/60">
              {!isKiosk && (
                <button
                  onClick={() => setActiveTab('fleet')}
                  className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all ${
                    activeTab === 'fleet'
                      ? 'bg-zinc-900 text-zinc-100 shadow-sm border border-zinc-800'
                      : 'text-zinc-400 hover:text-zinc-100'
                  }`}
                >
                  <Server size={14} className="text-indigo-400" /> {t('tabFleet')}
                </button>
              )}
              {!isKiosk && (
                <button
                  onClick={() => setActiveTab('schedule')}
                  className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all ${
                    activeTab === 'schedule'
                      ? 'bg-zinc-900 text-zinc-100 shadow-sm border border-zinc-800'
                      : 'text-zinc-400 hover:text-zinc-100'
                  }`}
                >
                  <Calendar size={14} className="text-indigo-400" /> {t('tabSchedule')}
                </button>
              )}
              <button
                onClick={() => setActiveTab('flasher')}
                className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all ${
                  activeTab === 'flasher'
                    ? 'bg-zinc-900 text-zinc-100 shadow-sm border border-zinc-800'
                    : 'text-zinc-400 hover:text-zinc-100'
                }`}
              >
                <HardDrive size={14} className="text-indigo-400" /> {t('tabFlasher')}
              </button>
              <button
                onClick={() => setActiveTab('history')}
                className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all ${
                  activeTab === 'history'
                    ? 'bg-zinc-900 text-zinc-100 shadow-sm border border-zinc-800'
                    : 'text-zinc-400 hover:text-zinc-100'
                }`}
              >
                <History size={14} className="text-indigo-400" /> {t('tabHistory')}
              </button>
              <button
                onClick={() => setActiveTab('logs')}
                className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all ${
                  activeTab === 'logs'
                    ? 'bg-zinc-900 text-zinc-100 shadow-sm border border-zinc-800'
                    : 'text-zinc-400 hover:text-zinc-100'
                }`}
              >
                <Terminal size={14} className="text-indigo-400" /> {t('tabLogs')}
              </button>
              {!isKiosk && (
                <button
                  onClick={() => setActiveTab('settings')}
                  className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all ${
                    activeTab === 'settings'
                      ? 'bg-zinc-900 text-zinc-100 shadow-sm border border-zinc-800'
                      : 'text-zinc-400 hover:text-zinc-100'
                  }`}
                >
                  <Gear size={14} className="text-indigo-400" /> {t('tabSettings')}
                </button>
              )}
              {!isKiosk && (
                <button
                  onClick={() => setActiveTab('clientiso')}
                  className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all border ${
                    activeTab === 'clientiso'
                      ? 'bg-indigo-600 text-white shadow-sm border-indigo-500 hover:bg-indigo-500'
                      : 'bg-indigo-600/10 hover:bg-indigo-600/20 text-indigo-400 dark:text-indigo-300 border-indigo-500/30'
                  }`}
                >
                  <Cpu size={14} className={activeTab === 'clientiso' ? 'text-white' : 'text-indigo-400 dark:text-indigo-300'} />
                  <span>{t('tabLiveCdKiosks') || 'Live-CD & Kiosks'}</span>
                </button>
              )}
            </nav>
          </div>
        </div>
      </header>

      {/* Main Body */}
      <main className={`flex-1 max-w-7xl w-full mx-auto px-6 py-8 ${isKiosk ? 'pb-20' : ''}`}>
        <div key={activeTab} className="animate-fade-in">
          {renderTabContent()}
        </div>
      </main>

      {/* Kiosk Mode Footer */}
      {isKiosk && (
        <footer className="fixed bottom-0 left-0 right-0 z-40 bg-zinc-950/90 backdrop-blur-md border-t border-zinc-900 py-3 text-center text-xs text-zinc-500 flex flex-wrap items-center justify-center gap-4 animate-fade-in">
          <span>{t('kioskTitle')}</span>
          <span className="h-4 w-px bg-zinc-800" />
          <span>UUID: <span className="font-mono text-zinc-400 select-all font-bold">{kioskUuid || 'Generating...'}</span></span>
          <span className="h-4 w-px bg-zinc-800" />
          <div className="relative group flex items-center gap-1">
            <span>{t('configuredServer')}</span>
            <span className="text-indigo-400 font-bold border-b border-dashed border-indigo-400/50 cursor-help pb-[1px] hover:text-indigo-300 hover:border-indigo-300 transition-colors">
              {kioskOrchestratorIp || '127.0.0.1'}
            </span>
            {/* Tooltip for hover */}
            <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 hidden group-hover:flex flex-col items-center pointer-events-none z-50">
              <div className="bg-zinc-900 border border-zinc-800 text-zinc-300 text-[10px] py-1.5 px-3 rounded-lg shadow-xl font-mono whitespace-nowrap">
                <span className="text-zinc-500 font-semibold uppercase tracking-wider block text-[8px] mb-0.5 text-center">{t('keyphraseToken')}</span>
                <span className="text-amber-400 font-bold">{connectionKeyphrase || 'unknown'}</span>
              </div>
              <div className="w-2 h-2 bg-zinc-900 border-r border-b border-zinc-800 rotate-45 -mt-1" />
            </div>
          </div>
          {watchdogStatus?.detected && (
            <>
              <span className="h-4 w-px bg-zinc-800" />
              <div className="flex items-center gap-2">
                <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
                  watchdogStatus.frozen 
                    ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' 
                    : 'bg-rose-500/10 text-rose-400 border border-rose-500/20 animate-pulse'
                }`}>
                  {watchdogStatus.frozen ? t('watchdogFrozenBadge') : t('watchdogActiveBadge')}
                  {watchdogStatus.seconds_left !== null && !watchdogStatus.frozen ? ` (${watchdogStatus.seconds_left}s)` : ''}
                </span>
                <button
                  disabled={watchdogActionLoading}
                  onClick={watchdogStatus.frozen ? handleUnfreezeWatchdog : handleFreezeWatchdog}
                  className="px-2.5 py-1 bg-zinc-900 border border-zinc-800 hover:border-zinc-700 text-zinc-200 hover:text-white rounded text-[10px] font-bold transition-all flex items-center gap-1.5 cursor-pointer disabled:opacity-50"
                >
                  {watchdogActionLoading && <RefreshCw size={9} className="animate-spin" />}
                  {watchdogStatus.frozen ? t('watchdogUnfreezeButton') : t('watchdogFreezeButton')}
                </button>
              </div>
            </>
          )}
        </footer>
      )}

      {/* Watchdog Alert Modal */}
      {showWatchdogModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fade-in">
          <div className="w-full max-w-md p-6 bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl space-y-4 animate-modal-in">
            <div className="flex items-start gap-3 border-b border-zinc-800 pb-3">
              <div className="p-2 bg-rose-500/10 text-rose-400 border border-rose-500/20 rounded-lg shrink-0">
                <ShieldAlert size={20} className="animate-pulse" />
              </div>
              <div>
                <h3 className="text-base font-bold text-zinc-50 leading-tight">{t('watchdogTitle')}</h3>
                <p className="text-[10px] text-zinc-500 font-semibold uppercase tracking-wider mt-0.5">{watchdogStatus?.port}</p>
              </div>
            </div>
            <p className="text-xs text-zinc-300 leading-relaxed">
              {t('watchdogAlertText')}
            </p>
            <div className="flex justify-end gap-2 pt-2 border-t border-zinc-800">
              <button
                onClick={() => setShowWatchdogModal(false)}
                className="px-4 py-2 text-xs font-bold text-zinc-400 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors"
              >
                {t('closeButton') || 'Close'}
              </button>
              <button
                onClick={handleFreezeWatchdog}
                disabled={watchdogActionLoading}
                className="px-4 py-2 text-xs font-bold text-white bg-rose-600 hover:bg-rose-500 rounded-lg disabled:opacity-50 transition-colors flex items-center gap-1.5"
              >
                {watchdogActionLoading ? <RefreshCw size={12} className="animate-spin" /> : null}
                {t('watchdogFreezeButton')}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Network Settings Modal */}
      {showNetworkModal && (
        <NetworkSettingsModal onClose={() => setShowNetworkModal(false)} />
      )}

      {/* Pairing Modal */}
      {showPairingModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fade-in">
          <div className="w-full max-w-md p-6 bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl space-y-4 animate-modal-in">
            <div className="flex items-center gap-3 border-b border-zinc-800 pb-3">
              <div className="p-2 bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 rounded-lg">
                <Link2 size={20} className="animate-pulse" />
              </div>
              <div>
                <h3 className="text-base font-bold text-zinc-50 leading-tight">{t('linkOrchestratorTitle') || 'Connect to Orchestrator'}</h3>
                <p className="text-[10px] text-zinc-400 font-semibold uppercase tracking-wider">{t('linkOrchestratorSub') || 'Establish secure paired connection'}</p>
              </div>
            </div>

            <div className="bg-zinc-950 border border-zinc-800/80 p-3 rounded-xl flex items-center justify-between">
              <div>
                <span className="text-[9px] text-zinc-500 font-bold uppercase block mb-0.5">{t('thisKioskId') || 'This Kiosk ID'}</span>
                <span className="font-mono text-xs text-zinc-300 font-semibold select-all">{kioskUuid || 'Generating...'}</span>
              </div>
              <button
                onClick={() => {
                  navigator.clipboard.writeText(kioskUuid);
                  alert(t('copied') || 'Copied!');
                }}
                className="p-2 bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 text-zinc-400 hover:text-zinc-200 rounded-lg transition-colors cursor-pointer"
                title={t('copyToClipboard') || 'Copy to Clipboard'}
              >
                <Copy size={14} />
              </button>
            </div>

            <form onSubmit={handlePairingSubmit} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('orchestratorIpLabel') || 'Orchestrator IP Address'}</label>
                <input
                  type="text"
                  required
                  placeholder="e.g. 192.168.222.2"
                  value={pairingIp}
                  onChange={(e) => setPairingIp(e.target.value)}
                  className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:border-indigo-500 focus:outline-none transition-colors"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('pairKeyLabel') || 'Security Key (Format: ABCD-1234)'}</label>
                <input
                  type="text"
                  required
                  placeholder="XXXX-XXXX"
                  value={pairingKey}
                  onChange={(e) => setPairingKey(e.target.value.toUpperCase())}
                  className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-amber-400 font-bold text-sm tracking-widest focus:border-indigo-500 focus:outline-none transition-colors font-mono text-center placeholder:font-sans placeholder:tracking-normal"
                />
              </div>

              {pairingError && <div className="text-xs text-rose-400 bg-rose-500/10 border border-rose-500/20 p-3 rounded-lg">{pairingError}</div>}
              {pairingSuccess && <div className="text-xs text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 p-3 rounded-lg">{pairingSuccess}</div>}

              <div className="flex justify-end gap-2 pt-2 border-t border-zinc-800">
                <button
                  type="button"
                  onClick={() => setShowPairingModal(false)}
                  className="px-4 py-2 text-xs font-semibold text-zinc-400 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors cursor-pointer"
                >
                  {t('cancel') || 'Cancel'}
                </button>
                <button
                  type="submit"
                  disabled={pairingSubmitting}
                  className="px-4 py-2 text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg disabled:opacity-50 transition-colors cursor-pointer"
                >
                  {pairingSubmitting ? t('saving') : (t('connectButton') || 'Connect')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Active task console log stream overlay modal */}
      {logTaskId && (
        <TaskLogsModal
          taskId={logTaskId}
          title={logTaskTitle}
          timezone={settings?.timezone || 'Browser Local'}
          onClose={() => setLogTaskId(null)}
        />
      )}

      {/* IP Prompt Modal when there are no nodes */}
      {showIpPromptModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fade-in">
          <div className="w-full max-w-md p-6 bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl space-y-4 animate-modal-in">
            <div className="flex items-center gap-3 border-b border-zinc-800 pb-3">
              <div className="p-2 bg-amber-500/10 text-amber-400 border border-amber-500/20 rounded-lg">
                <Gear size={20} />
              </div>
              <div>
                <h3 className="text-base font-bold text-zinc-50 leading-tight">{t('welcomeSetup')}</h3>
                <p className="text-[10px] text-zinc-400 font-semibold uppercase tracking-wider">{t('configureOrchestratorIp')}</p>
              </div>
            </div>

            <div className="flex justify-center py-2 bg-zinc-950/60 rounded-xl border border-zinc-800/80">
              <img src="/edge_bro_logo.png" alt="Edge B.R.O. Logo" className="w-40 h-40 object-contain rounded-lg shadow-lg border border-indigo-500/20" />
            </div>

            <p className="text-xs text-zinc-300 leading-relaxed font-medium">
              {t('welcomeExplanation')}
            </p>

            <form onSubmit={handleSaveIp} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('orchestratorIpLabel')}</label>
                <DropdownTextInput
                  value={orchestratorIp}
                  onChange={setOrchestratorIp}
                  options={availableIps}
                  required
                  placeholder="e.g. 192.168.222.2 (IP accessible to edge nodes)"
                />
                <p className="text-[10px] text-zinc-500 mt-1">
                  {t('orchestratorIpHint')}
                </p>
              </div>

              <div className="flex justify-end gap-2 pt-2 border-t border-zinc-800">
                <button
                  type="button"
                  onClick={() => setShowIpPromptModal(false)}
                  className="px-4 py-2 text-xs font-semibold text-zinc-400 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors"
                >
                  {t('skip')}
                </button>
                <button
                  type="submit"
                  disabled={savingIp}
                  className="px-4 py-2 text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg disabled:opacity-50 transition-colors"
                >
                  {savingIp ? t('saving') : t('saveAndContinue')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

export default function App() {
  return (
    <TranslationProvider>
      <AppContent />
    </TranslationProvider>
  );
}
