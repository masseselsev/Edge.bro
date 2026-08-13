import React, { useState, useEffect, useRef } from 'react';
import { Server, HardDrive, History, Settings as Gear, Terminal, Cpu, Globe2, Wifi, LogOut, Calendar, Sun, Moon, Link2, Copy, ShieldAlert, RefreshCw, Loader2, User, ArrowDown, ArrowUp, AlertTriangle } from 'lucide-react';
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
import NotificationBell from './components/NotificationBell';
import LanguageSelector from './components/LanguageSelector';
import BlockedKioskScreen from './components/BlockedKioskScreen';
import IpPromptModal from './components/IpPromptModal';
import WatchdogModal from './components/WatchdogModal';
import MainFooter from './components/MainFooter';
import KioskFooter from './components/KioskFooter';

type Tab = 'fleet' | 'flasher' | 'history' | 'logs' | 'settings' | 'clientiso' | 'schedule';

// Persists across reloads so the empty-fleet nag stops once dismissed —
// only re-armed if the flag itself is cleared (e.g. cleared browser storage).
const IP_PROMPT_DISMISSED_KEY = 'edge_bro_ip_prompt_dismissed';

const getUsageColorClass = (percent: number): string => {
  if (percent >= 80) return 'text-rose-400 font-bold animate-pulse';
  if (percent >= 50) return 'text-amber-400 font-semibold';
  return 'text-emerald-400';
};

function AppContent() {
  const { t, language } = useTranslation();
  const [activeTab, setActiveTab] = useState<Tab>(() => {
    const saved = localStorage.getItem('activeTab') as Tab | null;
    const valid: Tab[] = ['fleet', 'flasher', 'history', 'logs', 'settings', 'clientiso', 'schedule'];
    return saved && valid.includes(saved) ? saved : 'fleet';
  });
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    const saved = localStorage.getItem('theme');
    return (saved === 'light' || saved === 'dark') ? saved : 'dark';
  });

  useEffect(() => {
    localStorage.setItem('activeTab', activeTab);
  }, [activeTab]);

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
  const [orchestratorReachable, setOrchestratorReachable] = useState<boolean | null>(null);
  const [userDismissedNetworkModal, setUserDismissedNetworkModal] = useState(false);
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
  const [healthWarnings, setHealthWarnings] = useState<{ code: string; message: string }[]>([]);

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
  const [kioskId, setKioskId] = useState('');
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

  const [bandwidth, setBandwidth] = useState<{
    rx_speed: number;
    tx_speed: number;
    rx_percent: number;
    tx_percent: number;
    cpu_usage: number;
    ram_usage: number;
  } | null>(null);

  const formatSpeed = (bytesPerSec: number): string => {
    if (bytesPerSec < 1024) return `${bytesPerSec.toFixed(0)} B/s`;
    if (bytesPerSec < 1024 * 1024) return `${(bytesPerSec / 1024).toFixed(1)} KB/s`;
    if (bytesPerSec < 1024 * 1024 * 1024) return `${(bytesPerSec / (1024 * 1024)).toFixed(1)} MB/s`;
    return `${(bytesPerSec / (1024 * 1024 * 1024)).toFixed(1)} GB/s`;
  };

  useEffect(() => {
    if (!isKiosk && !isAuthenticated) return;
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

  // Auto-show network modal when kiosk has no connection (after boot, once per session)
  useEffect(() => {
    if (!isKiosk || !appReady || networkStatus === null) return;

    const isConnected = !!(networkStatus?.wired?.connected || networkStatus?.wifi?.connected);

    if (!isConnected && !userDismissedNetworkModal) {
      setShowNetworkModal(true);
    }
  }, [networkStatus, appReady, isKiosk]);

  // Poll orchestrator reachability (kiosk mode only)
  useEffect(() => {
    if (!isKiosk || !appReady) return;

    const checkOrchestrator = async () => {
      try {
        const res = await fetch('/api/nodes');
        if (res.ok) {
          setOrchestratorReachable(true);
        } else {
          setOrchestratorReachable(false);
        }
      } catch {
        setOrchestratorReachable(false);
      }
    };

    checkOrchestrator();
    const interval = setInterval(checkOrchestrator, 15000);
    return () => clearInterval(interval);
  }, [isKiosk, appReady]);

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
    const fetchHealth = async () => {
      try {
        const res = await fetch('/api/health');
        if (res.ok) {
          const data = await res.json();
          if (data.warnings) {
            setHealthWarnings(data.warnings);
          }
        }
      } catch (err) {
        console.error('Failed to fetch health warnings:', err);
      }
    };
    fetchHealth();
    const interval = setInterval(fetchHealth, 10000);
    return () => clearInterval(interval);
  }, []);

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
            .then(res => {
              if (!res.ok) throw new Error('Failed to fetch nodes');
              return res.json();
            })
            .then(nodes => {
              const nodesList = Array.isArray(nodes) ? nodes : (nodes.nodes || []);
              if (nodesList.length === 0 && !localStorage.getItem(IP_PROMPT_DISMISSED_KEY)) {
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
            setKioskId(data.kiosk_id || '');
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
          .then(res => {
            if (!res.ok) throw new Error('Failed to fetch nodes');
            return res.json();
          })
          .then(nodes => {
            const nodesList = Array.isArray(nodes) ? nodes : (nodes.nodes || []);
            if (nodesList.length === 0 && !localStorage.getItem(IP_PROMPT_DISMISSED_KEY)) {
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
        localStorage.setItem(IP_PROMPT_DISMISSED_KEY, '1');
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
        return <HistoryTab onViewLogs={handleViewLogs} timezone={tz} isKiosk={isKiosk} />;
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
    <div className={`min-h-full flex flex-col font-sans ${isKiosk ? 'select-none' : ''}`}>
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
                <span className="bg-indigo-500/10 text-indigo-300 border border-indigo-500/20 px-2.5 py-1 rounded font-mono font-bold text-sm uppercase tracking-wider">Edge-B.R.O.</span>
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
              <div className="relative p-2 bg-indigo-600/15 border border-indigo-500/30 rounded-lg shadow-lg flex items-center justify-center w-10 h-10">
                <svg className="w-6 h-6 text-indigo-400 filter drop-shadow-[0_0_4px_rgba(99,102,241,0.6)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                </svg>
                <span className="absolute top-1.5 right-1.5 w-1.5 h-1.5 bg-emerald-500 rounded-full animate-ping"></span>
                <span className="absolute top-1.5 right-1.5 w-1.5 h-1.5 bg-emerald-500 rounded-full"></span>
              </div>
              <div>
                <h1 className="text-base font-bold text-zinc-50 tracking-tight leading-none flex items-center gap-2">
                  <span className="bg-indigo-500/10 text-indigo-300 border border-indigo-500/20 px-2 py-0.5 rounded font-mono font-bold text-xs uppercase tracking-wider">Edge-B.R.O.</span>
                  <span className="text-[10px] bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 px-1.5 py-0.5 rounded font-mono font-bold">{appVersion}</span>
                </h1>
                <p className="text-[9px] text-zinc-500 font-semibold mt-1.5 uppercase tracking-wider">
                  {t('appSubtitle')}
                </p>
              </div>
            </div>

            {/* Center: Server Metrics Widget — admin-only, orchestrator mode */}
            {!isKiosk && isAuthenticated && bandwidth && (
              <div className="flex-shrink-0 flex items-center gap-3 bg-zinc-950/40 border border-zinc-800/60 rounded-xl px-3 py-1.5 shadow-inner transition-all duration-300">
                {/* CPU Usage */}
                <div className="flex items-center gap-1.5" title="CPU Utilization">
                  <span className="text-[10px] text-zinc-500 uppercase tracking-wider font-bold font-mono">CPU</span>
                  <span className={`text-[11px] font-mono font-semibold transition-colors duration-500 ${getUsageColorClass(bandwidth.cpu_usage)}`}>
                    {bandwidth.cpu_usage.toFixed(0)}%
                  </span>
                </div>
                <div className="w-px h-3 bg-zinc-800" />
                
                {/* RAM Usage */}
                <div className="flex items-center gap-1.5" title="RAM Utilization">
                  <span className="text-[10px] text-zinc-500 uppercase tracking-wider font-bold font-mono">RAM</span>
                  <span className={`text-[11px] font-mono font-semibold transition-colors duration-500 ${getUsageColorClass(bandwidth.ram_usage)}`}>
                    {bandwidth.ram_usage.toFixed(0)}%
                  </span>
                </div>
                <div className="w-px h-3 bg-zinc-800" />
                
                {/* Download (Rx) */}
                <div className="flex items-center gap-1.5" title={t('bandwidthDownload')}>
                  <ArrowDown size={12} className={bandwidth.rx_speed > 1024 ? `${getUsageColorClass(bandwidth.rx_percent)} animate-pulse` : 'text-zinc-600'} />
                  <span className="text-[10px] text-zinc-500 uppercase tracking-wider font-bold font-mono">RX</span>
                  <span className={`text-[11px] font-mono font-semibold transition-colors duration-500 ${getUsageColorClass(bandwidth.rx_percent)}`}>
                    {formatSpeed(bandwidth.rx_speed)}
                  </span>
                  <span className={`text-[9px] font-mono ${getUsageColorClass(bandwidth.rx_percent)}`}>({bandwidth.rx_percent.toFixed(1)}%)</span>
                </div>
                <div className="w-px h-3 bg-zinc-800" />
                
                {/* Upload (Tx) */}
                <div className="flex items-center gap-1.5" title={t('bandwidthUpload')}>
                  <ArrowUp size={12} className={bandwidth.tx_speed > 1024 ? `${getUsageColorClass(bandwidth.tx_percent)} animate-pulse` : 'text-zinc-600'} />
                  <span className="text-[10px] text-zinc-500 uppercase tracking-wider font-bold font-mono">TX</span>
                  <span className={`text-[11px] font-mono font-semibold transition-colors duration-500 ${getUsageColorClass(bandwidth.tx_percent)}`}>
                    {formatSpeed(bandwidth.tx_speed)}
                  </span>
                  <span className={`text-[9px] font-mono ${getUsageColorClass(bandwidth.tx_percent)}`}>({bandwidth.tx_percent.toFixed(1)}%)</span>
                </div>
              </div>
            )}

            {/* Right: Actions + Custom Language Switcher Dropdown */}
            <div className="flex-1 flex flex-wrap items-center justify-center md:justify-end gap-3">
              {isKiosk && (
                <>
                  <div className="flex items-center bg-zinc-950 p-1 rounded-xl border border-zinc-800/80 shadow-inner">
                    <button
                      onClick={() => restoreMode !== 'online' && handleToggleMode()}
                      className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition-all duration-300 cursor-pointer ${
                        restoreMode === 'online'
                          ? 'bg-gradient-to-r from-emerald-500 to-teal-600 text-white shadow-md shadow-emerald-950/50 scale-105'
                          : 'text-zinc-500 hover:text-zinc-400'
                      }`}
                    >
                      <Globe2 size={13} className={restoreMode === 'online' ? 'animate-pulse' : ''} />
                      <span>{t('modeOnline')}</span>
                    </button>
                    <button
                      onClick={() => restoreMode !== 'offline' && handleToggleMode()}
                      className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition-all duration-300 cursor-pointer ${
                        restoreMode === 'offline'
                          ? 'bg-gradient-to-r from-amber-500 to-orange-600 text-white shadow-md shadow-amber-950/50 scale-105'
                          : 'text-zinc-500 hover:text-zinc-400'
                      }`}
                    >
                      <HardDrive size={13} />
                      <span>{t('modeOffline')}</span>
                    </button>
                  </div>

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
                  {/* Server unreachable indicator: shown when network is up but orchestrator is down */}
                  {(networkStatus?.wired?.connected || networkStatus?.wifi?.connected) && orchestratorReachable === false && (
                    <span className="flex items-center gap-1 text-[10px] text-amber-400 font-bold bg-amber-500/10 border border-amber-500/20 px-2 py-0.5 rounded-full animate-fade-in">
                      <AlertTriangle size={10} />
                      {t('serverUnreachable')}
                    </span>
                  )}
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
                  <div className="mr-1">
                    <NotificationBell timezone={settings?.timezone || 'Browser Local'} />
                  </div>
                )}
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
      <main className={`flex-1 max-w-7xl w-full mx-auto px-6 py-8 ${isKiosk ? (restoreMode === 'online' && kioskStatus !== 'APPROVED' ? 'pb-28' : 'pb-20') : 'pb-20'}`}>
        <div key={activeTab} className="animate-tab-in">
          {renderTabContent()}
        </div>
      </main>

      {!isKiosk && (
        <MainFooter
          appVersion={appVersion}
          healthWarnings={healthWarnings}
          setActiveTab={setActiveTab}
        />
      )}

      {/* Kiosk Mode Footer */}
      {isKiosk && (
        <KioskFooter
          restoreMode={restoreMode}
          kioskStatus={kioskStatus}
          activationMsg={activationMsg}
          activationError={activationError}
          handleRequestActivation={handleRequestActivation}
          requestingActivation={requestingActivation}
          setPairingIp={setPairingIp}
          setPairingKey={setPairingKey}
          setPairingError={setPairingError}
          setPairingSuccess={setPairingSuccess}
          setShowPairingModal={setShowPairingModal}
          kioskOrchestratorIp={kioskOrchestratorIp}
          kioskId={kioskId}
          connectionKeyphrase={connectionKeyphrase}
          watchdogStatus={watchdogStatus}
          watchdogActionLoading={watchdogActionLoading}
          handleUnfreezeWatchdog={handleUnfreezeWatchdog}
          handleFreezeWatchdog={handleFreezeWatchdog}
          healthWarnings={healthWarnings}
        />
      )}

      {/* Watchdog Alert Modal */}
      {showWatchdogModal && (
        <WatchdogModal
          onClose={() => setShowWatchdogModal(false)}
          onFreeze={handleFreezeWatchdog}
          watchdogStatus={watchdogStatus}
          watchdogActionLoading={watchdogActionLoading}
        />
      )}

      {/* Network Settings Modal */}
      {showNetworkModal && (
        <NetworkSettingsModal
          onClose={() => {
            setShowNetworkModal(false);
            setUserDismissedNetworkModal(true);
          }}
          initialStatus={networkStatus}
          showNoNetworkWarning={isKiosk && !(networkStatus?.wired?.connected || networkStatus?.wifi?.connected)}
        />
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
                <span className="font-mono text-xs text-zinc-300 font-semibold select-all">{kioskId || 'Generating...'}</span>
              </div>
              <button
                onClick={() => {
                  navigator.clipboard.writeText(kioskId);
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
        <IpPromptModal
          onClose={() => {
            localStorage.setItem(IP_PROMPT_DISMISSED_KEY, '1');
            setShowIpPromptModal(false);
          }}
          onSubmit={handleSaveIp}
          orchestratorIp={orchestratorIp}
          setOrchestratorIp={setOrchestratorIp}
          availableIps={availableIps}
          savingIp={savingIp}
        />
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
