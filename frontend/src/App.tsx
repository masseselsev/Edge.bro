import React, { useState, useEffect, useRef } from 'react';
import { Server, HardDrive, History, Settings as Gear, Terminal, Cpu, Globe2, Wifi, LogOut, Calendar, Sun, Moon, Link2, Copy, ShieldAlert, RefreshCw, Loader2, User, ArrowDown, ArrowUp } from 'lucide-react';
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
import Login from './components/Login';
import ProfileModal from './components/ProfileModal';

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

function BlockedKioskScreen({ 
  status, 
  onActivationRequested, 
  onPairingSuccess,
  appVersion,
  kioskUuid
}: { 
  status: string; 
  onActivationRequested: () => void; 
  onPairingSuccess: () => void;
  appVersion: string;
  kioskUuid: string;
}) {
  const { t, language } = useTranslation();
  const [requesting, setRequesting] = useState(false);
  const [msg, setMsg] = useState('');
  const [errorMsg, setErrorMsg] = useState('');

  const [showPairing, setShowPairing] = useState(false);
  const [pairingIp, setPairingIp] = useState('');
  const [pairingKey, setPairingKey] = useState('');
  const [pairingSubmitting, setPairingSubmitting] = useState(false);

  const handleRequest = async () => {
    setRequesting(true);
    setMsg('');
    setErrorMsg('');
    try {
      const res = await fetch('/api/kiosk/request-activation', { method: 'POST' });
      if (res.ok) {
        setMsg(t('kioskBlockedSuccess') || 'Request submitted successfully!');
        onActivationRequested();
      } else {
        const data = await res.json();
        setErrorMsg(data.detail || t('kioskBlockedError') || 'Failed to submit request.');
      }
    } catch (err: any) {
      setErrorMsg(err.message || t('kioskBlockedError') || 'Failed to submit request.');
    } finally {
      setRequesting(false);
    }
  };

  const handlePairingSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setPairingSubmitting(true);
    setMsg('');
    setErrorMsg('');
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
      setMsg(t('kioskPairingSuccess') || 'Connected and paired successfully!');
      setTimeout(() => {
        onPairingSuccess();
      }, 1500);
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to connect to orchestrator');
    } finally {
      setPairingSubmitting(false);
    }
  };

  return (
    <div className="w-full flex items-center justify-center p-4">
      <div className="max-w-md w-full p-8 bg-zinc-900/50 border border-zinc-800/80 rounded-3xl shadow-2xl space-y-6 text-center animate-fade-in relative overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-b from-indigo-500/5 via-transparent to-transparent pointer-events-none" />

          
          {/* Status Icon */}
          <div className="flex justify-center">
            {status === 'PENDING' ? (
              <div className="relative p-5 bg-indigo-500/10 border border-indigo-500/20 rounded-2xl animate-pulse">
                <Loader2 size={36} className="text-indigo-400 animate-spin" strokeWidth={2.5} />
              </div>
            ) : (
              <div className="relative p-5 bg-red-500/15 border border-red-500/30 rounded-2xl shadow-lg">
                <ShieldAlert size={36} className="text-red-400 filter drop-shadow-[0_0_8px_rgba(239,68,68,0.5)]" strokeWidth={2} />
              </div>
            )}
          </div>

          {/* Title and Descriptions */}
          <div className="space-y-2">
            <h2 className="text-xl font-black text-zinc-150 tracking-tight">
              {status === 'PENDING' ? t('kioskBlockedPendingTitle') : t('kioskBlockedTitle')}
            </h2>
            <p className="text-sm font-semibold text-zinc-400">
              {status === 'PENDING' ? t('kioskBlockedPendingSub') : t('kioskBlockedSub')}
            </p>
            <p className="text-xs text-zinc-400 leading-relaxed font-medium">
              {status === 'PENDING' 
                ? t('kioskBlockedPendingDesc') 
                : (t('kioskBlockedDesc') || 'Please contact the administrator or request activation below.')
              }
            </p>
          </div>

          {/* Action button / Status messages */}
          <div className="pt-2">
            {!showPairing ? (
              <>
                {status !== 'PENDING' ? (
                  <button
                    onClick={handleRequest}
                    disabled={requesting}
                    className="w-full flex items-center justify-center gap-2 py-3 px-4 bg-gradient-to-r from-indigo-600 to-indigo-700 hover:from-indigo-500 hover:to-indigo-600 text-zinc-50 rounded-xl text-sm font-bold shadow-lg shadow-indigo-600/15 border border-indigo-500/30 transition-all duration-200 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed hover:-translate-y-0.5 active:translate-y-0 active:scale-[0.98]"
                  >
                    {requesting ? (
                      <>
                        <Loader2 size={16} className="animate-spin text-zinc-50" />
                        <span>{t('saving') || 'Submitting...'}</span>
                      </>
                    ) : (
                      <span>{t('kioskBlockedRequest')}</span>
                    )}
                  </button>
                ) : null}

                <button
                  onClick={() => {
                    setPairingIp(window.location.hostname);
                    setPairingKey('');
                    setShowPairing(true);
                  }}
                  className="mt-4 text-xs font-bold text-indigo-400 hover:text-indigo-300 transition-colors flex items-center justify-center gap-1.5 w-full cursor-pointer"
                >
                  <Link2 size={14} />
                  {t('kioskPairOtherServer') || 'Pair with another server'}
                </button>
              </>
            ) : (
              <form onSubmit={handlePairingSubmit} className="space-y-4 text-left border-t border-zinc-800 pt-4 mt-2 animate-fade-in">
                <div className="text-xs font-bold text-zinc-300 mb-2 uppercase tracking-wide">
                  {t('kioskPairOtherServer') || 'Pair with another server'}
                </div>
                <div>
                  <label className="block text-[10px] font-semibold text-zinc-400 mb-1">
                    {t('kioskPairingIpLabel') || 'New Orchestrator IP'}
                  </label>
                  <input
                    type="text"
                    required
                    placeholder="e.g. 192.168.1.100"
                    value={pairingIp}
                    onChange={(e) => setPairingIp(e.target.value)}
                    className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-150 text-xs focus:border-indigo-500 focus:outline-none transition-colors"
                  />
                </div>
                <div>
                  <label className="block text-[10px] font-semibold text-zinc-400 mb-1">
                    {t('kioskPairingKeyLabel') || 'Pairing Key (1234AB)'}
                  </label>
                  <input
                    type="text"
                    required
                    placeholder="e.g. 1234AB"
                    value={pairingKey}
                    onChange={(e) => setPairingKey(e.target.value)}
                    className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-150 text-xs focus:border-indigo-500 focus:outline-none transition-colors"
                  />
                </div>
                <div className="flex gap-2 pt-2">
                  <button
                    type="button"
                    onClick={() => setShowPairing(false)}
                    className="flex-1 py-2 text-xs font-bold text-zinc-400 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors cursor-pointer text-center"
                  >
                    {t('cancel') || 'Cancel'}
                  </button>
                  <button
                    type="submit"
                    disabled={pairingSubmitting}
                    className="flex-1 py-2 text-xs font-bold text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg disabled:opacity-50 transition-colors cursor-pointer text-center flex items-center justify-center gap-1.5"
                  >
                    {pairingSubmitting && <Loader2 size={12} className="animate-spin" />}
                    {t('kioskPairButton') || 'Connect & Pair'}
                  </button>
                </div>
              </form>
            )}

            {msg && (
              <div className="mt-3 p-3 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs rounded-xl font-bold animate-fade-in">
                {msg}
              </div>
            )}
            {errorMsg && (
              <div className="mt-3 p-3 bg-red-500/10 border border-red-500/20 text-red-400 text-xs rounded-xl font-bold animate-fade-in">
                {errorMsg}
              </div>
            )}
          </div>
        {/* Footer Info inside card */}
        <div className="text-center pt-4 border-t border-zinc-800/50 space-y-1">
          <span className="text-[9px] text-zinc-400 font-bold uppercase tracking-wider block">
            {t('kioskBlockedThisId')}
          </span>
          <span className="font-mono text-xs font-black text-indigo-400 bg-indigo-500/5 border border-indigo-500/10 px-3 py-1 rounded-lg inline-block">
            {kioskUuid || 'UNKNOWN'}
          </span>
        </div>
      </div>
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
  const [networkLoaded, setNetworkLoaded] = useState(false);
  const [logTaskId, setLogTaskId] = useState<string | null>(null);
  const [logTaskTitle, setLogTaskTitle] = useState<string>('');
  
  const [showIpPromptModal, setShowIpPromptModal] = useState(false);
  const [orchestratorIp, setOrchestratorIp] = useState('');
  const [settings, setSettings] = useState<any>(null);
  const [availableIps, setAvailableIps] = useState<string[]>([]);
  const [savingIp, setSavingIp] = useState(false);
  const [appVersion, setAppVersion] = useState('');
  const [isKiosk, setIsKiosk] = useState(false);
  const [appReady, setAppReady] = useState(false);
  const [versionLoaded, setVersionLoaded] = useState(false);
  const [settingsLoaded, setSettingsLoaded] = useState(false);
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
  const [kioskStatus, setKioskStatus] = useState('APPROVED');
  const [showPairingModal, setShowPairingModal] = useState(false);
  const [pairingIp, setPairingIp] = useState('');
  const [pairingKey, setPairingKey] = useState('');
  const [pairingSubmitting, setPairingSubmitting] = useState(false);
  const [pairingError, setPairingError] = useState('');
  const [pairingSuccess, setPairingSuccess] = useState('');
  const [requestingActivation, setRequestingActivation] = useState(false);
  const [activationMsg, setActivationMsg] = useState('');
  const [activationError, setActivationError] = useState('');
  const [pendingKiosks, setPendingKiosks] = useState<any[]>([]);
  const [activeReviewKiosk, setActiveReviewKiosk] = useState<any | null>(null);
  const [pairingMode, setPairingMode] = useState<'enroll' | 'connect'>('enroll');
  const [availableServerIps, setAvailableServerIps] = useState<string[]>([]);
  const [kioskName, setKioskName] = useState('');
  const [kioskPhone, setKioskPhone] = useState('');
  const [kioskComment, setKioskComment] = useState('');
  const [enrollMsg, setEnrollMsg] = useState('');

  // Authentication states
  const [currentUser, setCurrentUser] = useState<any>(null);
  const [isAuthenticated, setIsAuthenticated] = useState<boolean | null>(null);
  const [profileDropdownOpen, setProfileDropdownOpen] = useState(false);
  const [showProfileModal, setShowProfileModal] = useState(false);
  const profileDropdownRef = useRef<HTMLDivElement>(null);

  // Bandwidth monitoring state (admin-only, non-kiosk)
  const [bandwidth, setBandwidth] = useState<{ rx_speed: number; tx_speed: number } | null>(null);

  const formatSpeed = (bytesPerSec: number): string => {
    if (bytesPerSec < 1024) return `${bytesPerSec.toFixed(0)} B/s`;
    if (bytesPerSec < 1024 * 1024) return `${(bytesPerSec / 1024).toFixed(1)} KB/s`;
    if (bytesPerSec < 1024 * 1024 * 1024) return `${(bytesPerSec / (1024 * 1024)).toFixed(1)} MB/s`;
    return `${(bytesPerSec / (1024 * 1024 * 1024)).toFixed(1)} GB/s`;
  };

  useEffect(() => {
    if (!isAuthenticated || isKiosk) return;
    const fetchBandwidth = async () => {
      try {
        const res = await fetch('/api/network/bandwidth');
        if (res.ok) {
          const data = await res.json();
          setBandwidth(data);
        }
      } catch (err) {
        console.error('Failed to fetch bandwidth:', err);
      }
    };
    fetchBandwidth();
    const interval = setInterval(fetchBandwidth, 3000);
    return () => clearInterval(interval);
  }, [isAuthenticated, isKiosk]);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (profileDropdownRef.current && !profileDropdownRef.current.contains(event.target as Node)) {
        setProfileDropdownOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleLogout = async () => {
    try {
      await fetch('/api/auth/logout', { method: 'POST' });
      window.location.reload();
    } catch (err) {
      console.error(err);
    }
  };

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

  const handleRequestActivation = async () => {
    setRequestingActivation(true);
    setActivationMsg('');
    setActivationError('');
    try {
      const res = await fetch('/api/kiosk/request-activation', { method: 'POST' });
      if (res.ok) {
        setActivationMsg(t('kioskBlockedSuccess') || 'Request submitted successfully!');
        setKioskStatus('PENDING');
      } else {
        const data = await res.json();
        setActivationError(data.detail || t('kioskBlockedError') || 'Failed to submit request.');
      }
    } catch (err: any) {
      setActivationError(err.message || t('kioskBlockedError') || 'Failed to submit request.');
    } finally {
      setRequestingActivation(false);
    }
  };

  const handleEnrollSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setPairingSubmitting(true);
    setPairingError('');
    setEnrollMsg('');
    try {
      const res = await fetch('/api/kiosk/enroll', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          orchestrator_ip: pairingIp.trim(),
          name: kioskName.trim(),
          phone: kioskPhone.trim(),
          comment: kioskComment.trim()
        })
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Enrollment request failed');
      }
      setEnrollMsg(t('enrollStatusPending') || 'Connection request submitted successfully! Waiting for server administrator approval.');
    } catch (err: any) {
      setPairingError(err.message);
    } finally {
      setPairingSubmitting(false);
    }
  };

  const handleApproveKiosk = async (id: number) => {
    try {
      const res = await fetch(`/api/kiosks/${id}/toggle-active`, { method: 'POST' });
      if (res.ok) {
        const refreshRes = await fetch('/api/kiosks');
        if (refreshRes.ok) {
          const data = await refreshRes.json();
          const pending = data.filter((k: any) => k.status === 'PENDING');
          setPendingKiosks(pending);
        }
        setActiveReviewKiosk(null);
        window.dispatchEvent(new CustomEvent('kiosks-updated'));
      } else {
        const data = await res.json();
        alert(data.detail || 'Failed to approve kiosk');
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleRejectKiosk = async (id: number) => {
    if (window.confirm(t('kioskRevokeConfirm') || 'Are you sure you want to reject this request?')) {
      try {
        const res = await fetch(`/api/kiosks/${id}`, { method: 'DELETE' });
        if (res.ok) {
          const refreshRes = await fetch('/api/kiosks');
          if (refreshRes.ok) {
            const data = await refreshRes.json();
            const pending = data.filter((k: any) => k.status === 'PENDING');
            setPendingKiosks(pending);
          }
          setActiveReviewKiosk(null);
          window.dispatchEvent(new CustomEvent('kiosks-updated'));
        } else {
          const data = await res.json();
          alert(data.detail || 'Failed to reject request');
        }
      } catch (err) {
        console.error(err);
      }
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

    const pollKioskStatus = async () => {
      try {
        const res = await fetch('/api/version');
        if (res.ok) {
          const data = await res.json();
          if (data && data.kiosk_status) {
            setKioskStatus(data.kiosk_status);
          }
        }
      } catch (err) {
        console.error('Failed to poll kiosk status:', err);
      }
    };

    pollKioskStatus();
    const interval = setInterval(pollKioskStatus, 8000);
    return () => clearInterval(interval);
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
    // Initial status was already fetched during the boot loading phase
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
    let retryCount = 0;
    
    const loadSettingsAndNodes = () => {
      fetch('/api/settings')
        .then(res => {
          if (!res.ok) throw new Error('Failed to fetch settings');
          return res.json();
        })
        .then(sett => {
          setSettings(sett);
          setOrchestratorIp(sett.orchestrator_ip || '');
          setAvailableIps(sett.available_ips || []);
          
          fetch('/api/nodes')
            .then(res => res.json())
            .then(nodes => {
              if (nodes.length === 0) {
                setShowIpPromptModal(true);
              }
            })
            .catch(err => console.error(err))
            .finally(() => setSettingsLoaded(true));
        })
        .catch(err => {
          console.error(err);
          setSettingsLoaded(true);
        });
    };

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
            setIsAuthenticated(true);
            setActiveTab('flasher');
            setKioskOrchestratorIp(data.orchestrator_ip || '');
            setConnectionKeyphrase(data.auth_token || '');
            setKioskUuid(data.kiosk_uuid || '');
            setAvailableServerIps(data.available_server_ips || []);
            if (data.kiosk_status) {
              setKioskStatus(data.kiosk_status);
            }
            
            // Kiosk mode: pre-fetch network status
            fetch('/api/network/status')
              .then(res => {
                if (res.ok) return res.json();
                throw new Error('Failed to fetch network status');
              })
              .then(netData => {
                setNetworkStatus(netData);
              })
              .catch(err => console.error('Failed to pre-fetch network status:', err))
              .finally(() => {
                setNetworkLoaded(true);
                setVersionLoaded(true);
              });
            loadSettingsAndNodes();
          } else {
            // Not kiosk: check auth status
            fetch('/api/auth/me')
              .then(res => {
                if (res.ok) return res.json();
                throw new Error('Not authenticated');
              })
              .then(user => {
                setCurrentUser(user);
                setIsAuthenticated(true);
                setNetworkLoaded(true);
                setVersionLoaded(true);
                loadSettingsAndNodes();
              })
              .catch(() => {
                setIsAuthenticated(false);
                setNetworkLoaded(true);
                setVersionLoaded(true);
                setSettingsLoaded(true); // Don't block with loading screen if not authenticated
              });
          }
        })
        .catch(err => {
          console.error('Error fetching version:', err);
          if (retryCount < 5) {
            retryCount++;
            setTimeout(fetchVersion, 3000);
          } else {
            setNetworkLoaded(true);
            setVersionLoaded(true);
            setSettingsLoaded(true);
          }
        });
    };
    fetchVersion();
  }, []);

  useEffect(() => {
    if (!isAuthenticated || isKiosk) return;

    const fetchPendingKiosks = async () => {
      try {
        const res = await fetch('/api/kiosks');
        if (res.ok) {
          const data = await res.json();
          const pending = data.filter((k: any) => k.status === 'PENDING');
          setPendingKiosks(pending);
        }
      } catch (err) {
        console.error('Failed to fetch pending kiosks:', err);
      }
    };

    fetchPendingKiosks();
    const interval = setInterval(fetchPendingKiosks, 10000);
    return () => clearInterval(interval);
  }, [isAuthenticated, isKiosk]);

  const handleLoginSuccess = (user: any) => {
    setCurrentUser(user);
    setIsAuthenticated(true);
    setSettingsLoaded(false);
    fetch('/api/settings')
      .then(res => res.json())
      .then(sett => {
        setSettings(sett);
        setOrchestratorIp(sett.orchestrator_ip || '');
        setAvailableIps(sett.available_ips || []);
        
        fetch('/api/nodes')
          .then(res => res.json())
          .then(nodes => {
            if (nodes.length === 0) {
              setShowIpPromptModal(true);
            }
          })
          .catch(err => console.error(err))
          .finally(() => setSettingsLoaded(true));
      })
      .catch(err => {
        console.error(err);
        setSettingsLoaded(true);
      });
  };

  // Mark app as ready once critical data is loaded
  useEffect(() => {
    if (versionLoaded && settingsLoaded && networkLoaded) {
      // Small delay to let the UI render before removing the overlay
      const timer = setTimeout(() => setAppReady(true), 300);
      return () => clearTimeout(timer);
    }
  }, [versionLoaded, settingsLoaded, networkLoaded]);

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
        return <FlasherTab onViewLogs={handleViewLogs} timezone={tz} restoreMode={restoreMode} isKiosk={isKiosk} kioskStatus={kioskStatus} />;
      case 'clientiso':
        return <ClientIsoTab onViewLogs={handleViewLogs} />;
      case 'history':
        return <HistoryTab onViewLogs={handleViewLogs} timezone={tz} />;
      case 'logs':
        return <LogsTab onViewLogs={handleViewLogs} timezone={tz} isKiosk={isKiosk} />;
      case 'settings':
        return <SettingsTab onSettingsUpdated={setSettings} currentUser={currentUser} />;
      case 'schedule':
        return <ScheduleTab />;
      case 'fleet':
      default:
        return <FleetTab onViewLogs={handleViewLogs} timezone={tz} />;
    }
  };

  if (isAuthenticated === false && !isKiosk) {
    return <Login onLoginSuccess={handleLoginSuccess} />;
  }

  // Render main app layout unconditionally once appReady is true.

  return (
    <div className="min-h-full flex flex-col font-sans select-none">
      {/* Boot Loading Overlay */}
      {!appReady && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-zinc-950/95 backdrop-blur-xl transition-opacity duration-500">
          <div className="flex flex-col items-center gap-5 animate-fade-in">
            <div className="relative p-4 bg-indigo-600/15 border border-indigo-500/30 rounded-2xl shadow-2xl">
              <svg className="w-10 h-10 text-indigo-400 filter drop-shadow-[0_0_8px_rgba(99,102,241,0.6)] animate-pulse" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
              </svg>
              <span className="absolute top-2.5 right-2.5 w-2 h-2 bg-emerald-500 rounded-full animate-ping"></span>
              <span className="absolute top-2.5 right-2.5 w-2 h-2 bg-emerald-500 rounded-full"></span>
            </div>
            <div className="text-center space-y-2">
              <h2 className="text-lg font-bold text-zinc-100 tracking-tight">
                <span className="bg-indigo-500/10 text-indigo-300 border border-indigo-500/20 px-2.5 py-1 rounded font-mono font-bold text-sm uppercase tracking-wider">Edge B.R.O.</span>
              </h2>
              <div className="flex items-center justify-center gap-2 text-zinc-400 text-xs font-semibold">
                <Loader2 size={14} className="animate-spin text-indigo-400" />
                <span>{t('loadingInitializing')}</span>
              </div>
            </div>
          </div>
        </div>
      )}
      {/* Global Header */}
      <header className="bg-zinc-900/80 backdrop-blur-md border-b border-zinc-800/80 sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-6 py-3 space-y-3">
        {/* Row 1: Logo/Title | Bandwidth | Actions */}
          <div className="flex flex-col md:flex-row items-center justify-between gap-4">
            {/* Left: Brand Identity with SVG logo */}
            <div className="flex-1 flex items-center gap-3 justify-center md:justify-start">
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

            {/* Center: Bandwidth Widget — admin-only, orchestrator mode */}
            {!isKiosk && isAuthenticated && bandwidth && (
              <div className="flex-shrink-0 flex items-center gap-3 bg-zinc-950/40 border border-zinc-800/60 rounded-xl px-3 py-1.5 shadow-inner transition-all duration-300">
                {/* Download (Rx) */}
                <div className="flex items-center gap-1.5" title={t('bandwidthDownload')}>
                  <ArrowDown size={12} className={bandwidth.rx_speed > 1024 ? 'text-emerald-400 animate-pulse' : 'text-zinc-600'} />
                  <span className="text-[10px] text-zinc-500 uppercase tracking-wider font-bold font-mono">RX</span>
                  <span className={`text-[11px] font-mono font-semibold transition-colors duration-500 ${bandwidth.rx_speed > 1024 ? 'text-zinc-200' : 'text-zinc-500'}`}>
                    {formatSpeed(bandwidth.rx_speed)}
                  </span>
                </div>
                <div className="w-px h-3 bg-zinc-800" />
                {/* Upload (Tx) */}
                <div className="flex items-center gap-1.5" title={t('bandwidthUpload')}>
                  <ArrowUp size={12} className={bandwidth.tx_speed > 1024 ? 'text-indigo-400 animate-pulse' : 'text-zinc-600'} />
                  <span className="text-[10px] text-zinc-500 uppercase tracking-wider font-bold font-mono">TX</span>
                  <span className={`text-[11px] font-mono font-semibold transition-colors duration-500 ${bandwidth.tx_speed > 1024 ? 'text-zinc-200' : 'text-zinc-500'}`}>
                    {formatSpeed(bandwidth.tx_speed)}
                  </span>
                </div>
              </div>
            )}

            {/* Right: Actions + Custom Language Switcher Dropdown */}
            <div className="flex-1 flex flex-wrap items-center justify-center md:justify-end gap-3">
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
                {!isKiosk && isAuthenticated && currentUser && (
                  <div className="relative mr-1" ref={profileDropdownRef}>
                    <button
                      onClick={() => setProfileDropdownOpen(!profileDropdownOpen)}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-zinc-950 hover:bg-zinc-900 border border-zinc-800 text-xs text-zinc-300 font-bold transition-all duration-200 cursor-pointer outline-none"
                    >
                      <User size={13} className="text-zinc-400" />
                      <span>{currentUser.name || currentUser.username}</span>
                      <svg className={`w-3 h-3 text-zinc-500 transition-transform duration-200 ${profileDropdownOpen ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="3">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                      </svg>
                    </button>
                    {profileDropdownOpen && (
                      <div className="absolute right-0 mt-1.5 w-44 rounded-lg bg-zinc-900 border border-zinc-800 shadow-2xl p-1 z-50 origin-top-right animate-dropdown-in">
                        <button
                          onClick={() => {
                            setProfileDropdownOpen(false);
                            setShowProfileModal(true);
                          }}
                          className="w-full text-left px-3 py-2 text-xs font-semibold rounded-md text-zinc-300 hover:text-zinc-50 hover:bg-zinc-800 transition-colors cursor-pointer"
                        >
                          {t('editProfile') || 'Edit Profile'}
                        </button>
                        <button
                          onClick={handleLogout}
                          className="w-full text-left px-3 py-2 text-xs font-semibold rounded-md text-rose-450 hover:text-rose-400 hover:bg-rose-950/20 transition-colors border-t border-zinc-850 mt-1 pt-2 cursor-pointer"
                        >
                          {t('logoutButton') || 'Logout'}
                        </button>
                      </div>
                    )}
                  </div>
                )}
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

      {/* Pending Kiosk Connection requests banner */}
      {!isKiosk && pendingKiosks.length > 0 && (
        <div className="bg-zinc-950 border-b border-amber-500/20 py-2.5 px-6 shadow-md transition-all duration-300 ease-in-out animate-fade-in">
          <div className="max-w-7xl mx-auto flex items-center justify-between gap-4 flex-wrap">
            <div className="flex items-center gap-2 text-xs font-semibold text-zinc-300">
              <span className="flex h-2 w-2 relative">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2 w-2 bg-amber-500"></span>
              </span>
              <span>
                {t('pendingConnectionBanner')
                  .replace('{name}', pendingKiosks[0].name || t('unnamedKiosk') || 'Unnamed')
                  .replace('{phone}', pendingKiosks[0].phone || '')}
                {pendingKiosks.length > 1 ? ` (+${pendingKiosks.length - 1})` : ''}
              </span>
            </div>
            <button
              onClick={() => setActiveReviewKiosk(pendingKiosks[0])}
              className="px-3 py-1 bg-amber-500 hover:bg-amber-400 text-zinc-950 rounded text-[11px] font-bold transition-all duration-200 cursor-pointer shadow-[0_0_12px_rgba(245,158,11,0.2)] hover:shadow-[0_0_16px_rgba(245,158,11,0.4)]"
            >
              {t('reviewRequest') || 'Review Request'}
            </button>
          </div>
        </div>
      )}

      {/* Main Body */}
      <main className={`flex-1 max-w-7xl w-full mx-auto px-6 py-8 ${isKiosk ? (restoreMode === 'online' && kioskStatus !== 'APPROVED' ? 'pb-28' : 'pb-20') : ''}`}>
        <div key={activeTab} className="animate-fade-in">
          {renderTabContent()}
        </div>
      </main>

      {/* Kiosk Mode Footer */}
      {isKiosk && (
        <footer className="fixed bottom-0 left-0 right-0 z-40 bg-zinc-950/95 backdrop-blur-md border-t border-zinc-900 flex flex-col animate-fade-in">
          {/* Connection / Activation Bar (Horizontal) */}
          {restoreMode === 'online' && kioskStatus !== 'APPROVED' && (
            <div className="px-6 py-2.5 bg-indigo-950/10 border-b border-zinc-900/60 flex flex-wrap items-center justify-between gap-4 text-xs font-semibold">
              <div className="flex items-center gap-2">
                {kioskStatus === 'PENDING' ? (
                  <>
                    <Loader2 size={13} className="text-indigo-400 animate-spin" />
                    <span className="text-indigo-400 font-bold">{t('kioskBlockedPendingTitle') || 'Activation Request Pending'}</span>
                    <span className="h-3 w-px bg-zinc-800" />
                    <span className="text-[11px] text-zinc-400 font-medium">{t('kioskBlockedPendingSub') || 'Waiting for administrator approval.'}</span>
                  </>
                ) : (
                  <>
                    <ShieldAlert size={13} className="text-red-400" />
                    <span className="text-red-400 font-bold">{t('kioskBlockedTitle') || 'Kiosk Access Blocked'}</span>
                    <span className="h-3 w-px bg-zinc-800" />
                    <span className="text-[11px] text-zinc-400 font-medium">{t('kioskBlockedSub') || 'This kiosk terminal is not authorized. Request activation to connect.'}</span>
                  </>
                )}
              </div>
              
              <div className="flex items-center gap-3">
                {activationMsg && <span className="text-emerald-400 text-[11px] font-bold">{activationMsg}</span>}
                {activationError && <span className="text-red-400 text-[11px] font-bold">{activationError}</span>}
                
                {kioskStatus !== 'PENDING' && (
                  <button
                    onClick={handleRequestActivation}
                    disabled={requestingActivation}
                    className="px-3 py-1 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded font-bold text-[11px] transition-all cursor-pointer flex items-center gap-1.5 active:translate-y-0.5"
                  >
                    {requestingActivation && <Loader2 size={11} className="animate-spin" />}
                    {t('kioskBlockedRequest') || 'Request Activation'}
                  </button>
                )}
                
                <button
                  onClick={() => {
                    setPairingIp(kioskOrchestratorIp || window.location.hostname);
                    setPairingKey('');
                    setPairingError('');
                    setPairingSuccess('');
                    setShowPairingModal(true);
                  }}
                  className="px-3 py-1 bg-zinc-900 hover:bg-zinc-800 text-zinc-300 rounded border border-zinc-800 text-[11px] font-bold transition-all cursor-pointer flex items-center gap-1 active:translate-y-0.5"
                >
                  <Link2 size={11} />
                  {t('kioskPairOtherServer') || 'Pair with another server'}
                </button>
              </div>
            </div>
          )}

          {/* Main Footer Info */}
          <div className="py-3 text-center text-xs text-zinc-500 flex flex-wrap items-center justify-center gap-4">
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
          </div>
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
        <NetworkSettingsModal onClose={() => setShowNetworkModal(false)} initialStatus={networkStatus} />
      )}

      {/* Profile Modal */}
      {showProfileModal && currentUser && (
        <ProfileModal
          currentUser={currentUser}
          onClose={() => setShowProfileModal(false)}
          onUpdateSuccess={(updated) => setCurrentUser(updated)}
        />
      )}

      {/* Review Modal */}
      {activeReviewKiosk && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fade-in">
          <div className="w-full max-w-md p-6 bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl space-y-4 animate-modal-in">
            <div className="flex items-center gap-3 border-b border-zinc-800 pb-3">
              <div className="p-2 bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 rounded-lg">
                <Server size={20} className="animate-pulse" />
              </div>
              <div>
                <h3 className="text-base font-bold text-zinc-50 leading-tight">
                  {t('enrollmentModalTitle') || 'Pending Kiosk Connection Request'}
                </h3>
                <p className="text-[10px] text-zinc-400 font-semibold uppercase tracking-wider">
                  {activeReviewKiosk.name || t('unnamedKiosk') || 'Unnamed Kiosk'}
                </p>
              </div>
            </div>

            <div className="space-y-3 text-xs border-b border-zinc-850 pb-3">
              <div className="grid grid-cols-3 gap-2">
                <span className="text-zinc-500 font-semibold">{t('kioskPhone') || 'Phone'}:</span>
                <span className="col-span-2 text-zinc-300 font-medium">{activeReviewKiosk.phone || '—'}</span>
              </div>
              <div className="grid grid-cols-3 gap-2">
                <span className="text-zinc-500 font-semibold">{t('kioskComment') || 'Comment'}:</span>
                <span className="col-span-2 text-zinc-300 font-medium whitespace-pre-wrap">{activeReviewKiosk.comment || '—'}</span>
              </div>
              <div className="grid grid-cols-3 gap-2">
                <span className="text-zinc-500 font-semibold">UUID:</span>
                <span className="col-span-2 text-zinc-400 font-mono select-all break-all">
                  {activeReviewKiosk.uuid.startsWith('PENDING-') ? (
                    <span className="text-zinc-500 italic">{t('kioskPending') || 'Pending...'}</span>
                  ) : (
                    activeReviewKiosk.uuid
                  )}
                </span>
              </div>
            </div>

            <div className="bg-zinc-950 p-4 border border-zinc-850 rounded-xl space-y-2 text-center text-zinc-400">
              <p className="text-xs">
                {t('kioskApprovePrompt') || 'This kiosk is requesting connection. Click "Activate" to grant access.'}
              </p>
            </div>

            <div className="flex justify-end gap-2 pt-2 border-t border-zinc-800">
              <button
                type="button"
                onClick={() => handleRejectKiosk(activeReviewKiosk.id)}
                className="px-4 py-2 text-xs font-semibold text-rose-400 bg-rose-950/20 hover:bg-rose-950/40 border border-rose-900/30 rounded-lg transition-colors cursor-pointer"
              >
                {t('rejectKiosk') || 'Reject Kiosk'}
              </button>
              <button
                type="button"
                onClick={() => handleApproveKiosk(activeReviewKiosk.id)}
                className="px-4 py-2 text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors cursor-pointer"
              >
                {t('kioskActionEnable') || 'Approve & Activate'}
              </button>
              <button
                type="button"
                onClick={() => setActiveReviewKiosk(null)}
                className="px-4 py-2 text-xs font-semibold text-zinc-300 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors cursor-pointer"
              >
                {t('closeButton') || 'Close'}
              </button>
            </div>
          </div>
        </div>
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

            <div className="flex bg-zinc-950 rounded-lg p-1 gap-1 border border-zinc-800/40">
              <button
                type="button"
                onClick={() => {
                  setPairingMode('enroll');
                  setPairingError('');
                  setEnrollMsg('');
                }}
                className={`flex-1 py-1.5 text-[10px] font-bold rounded-md uppercase cursor-pointer transition-all ${
                  pairingMode === 'enroll'
                    ? 'bg-indigo-600 text-white shadow-sm'
                    : 'text-zinc-400 hover:text-zinc-50 hover:bg-zinc-900'
                }`}
              >
                {t('submitRequest') || 'Request Key'}
              </button>
              <button
                type="button"
                onClick={() => {
                  setPairingMode('connect');
                  setPairingError('');
                  setEnrollMsg('');
                }}
                className={`flex-1 py-1.5 text-[10px] font-bold rounded-md uppercase cursor-pointer transition-all ${
                  pairingMode === 'connect'
                    ? 'bg-indigo-600 text-white shadow-sm'
                    : 'text-zinc-400 hover:text-zinc-50 hover:bg-zinc-900'
                }`}
              >
                {t('enterPairingKey') || 'Enter Key'}
              </button>
            </div>

            {pairingMode === 'enroll' ? (
              <form onSubmit={handleEnrollSubmit} className="space-y-4">
                {enrollMsg && (
                  <div className="text-xs text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 p-3 rounded-lg leading-relaxed">
                    {enrollMsg}
                  </div>
                )}
                <div>
                  <label className="block text-xs font-semibold text-zinc-400 mb-1.5">
                    {t('selectServerIp') || 'Select Server IP'}
                  </label>
                  <DropdownTextInput
                    value={pairingIp}
                    onChange={setPairingIp}
                    options={availableServerIps}
                    required
                  />
                </div>

                <div>
                  <label className="block text-xs font-semibold text-zinc-400 mb-1.5">
                    {t('kioskNameLabel') || 'Friendly Name'}
                  </label>
                  <input
                    type="text"
                    required
                    placeholder={t('kioskNewNamePlaceholder') || 'e.g. Front desk kiosk'}
                    value={kioskName}
                    onChange={(e) => setKioskName(e.target.value)}
                    className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:border-indigo-500 focus:outline-none transition-colors"
                  />
                </div>

                <div>
                  <label className="block text-xs font-semibold text-zinc-400 mb-1.5">
                    {t('kioskPhone') || 'Phone'}
                  </label>
                  <input
                    type="text"
                    required
                    placeholder={t('kioskPhonePlaceholder') || 'e.g. +1 555-0199'}
                    value={kioskPhone}
                    onChange={(e) => setKioskPhone(e.target.value)}
                    className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:border-indigo-500 focus:outline-none transition-colors"
                  />
                </div>

                <div>
                  <label className="block text-xs font-semibold text-zinc-400 mb-1.5">
                    {t('kioskComment') || 'Comment'}
                  </label>
                  <textarea
                    rows={2}
                    required
                    placeholder={t('kioskCommentPlaceholder') || 'e.g. Backup kiosk for first floor'}
                    value={kioskComment}
                    onChange={(e) => setKioskComment(e.target.value)}
                    className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:border-indigo-500 focus:outline-none transition-colors"
                  />
                </div>

                {pairingError && <div className="text-xs text-rose-455 bg-rose-500/10 border border-rose-500/20 p-3 rounded-lg">{pairingError}</div>}

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
                    {pairingSubmitting ? t('saving') : (t('submitRequest') || 'Submit Request')}
                  </button>
                </div>
              </form>
            ) : (
              <form onSubmit={handlePairingSubmit} className="space-y-4">
                {enrollMsg && (
                  <div className="text-xs text-amber-400 bg-amber-500/10 border border-amber-500/20 p-3 rounded-lg leading-relaxed">
                    {enrollMsg}
                  </div>
                )}

                <div>
                  <label className="block text-xs font-semibold text-zinc-400 mb-1.5">
                    {t('selectServerIp') || 'Select Server IP'}
                  </label>
                  <DropdownTextInput
                    value={pairingIp}
                    onChange={setPairingIp}
                    options={availableServerIps}
                    required
                  />
                </div>

                <div>
                  <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('pairKeyLabel') || 'Security Key (Format: 1234AB)'}</label>
                  <input
                    type="text"
                    required
                    placeholder="1234AB"
                    value={pairingKey}
                    onChange={(e) => setPairingKey(e.target.value.toUpperCase())}
                    className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-amber-400 font-bold text-sm tracking-widest focus:border-indigo-500 focus:outline-none transition-colors font-mono text-center placeholder:font-sans placeholder:tracking-normal"
                  />
                </div>

                {pairingError && <div className="text-xs text-rose-455 bg-rose-500/10 border border-rose-500/20 p-3 rounded-lg">{pairingError}</div>}
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
            )}
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
          bandwidth={bandwidth}
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
