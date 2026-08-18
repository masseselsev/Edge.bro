import React, { useEffect, useRef, useState } from 'react';
import {
  HardDrive, Globe2, Wifi, LogOut, Sun, Moon, Link2, User, ArrowDown, ArrowUp, AlertTriangle,
} from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';
import LanguageSelector from './LanguageSelector';
import NotificationBell from './NotificationBell';
import { TABS, type Tab } from '../tabs';
import { formatBytesPerSecond } from './formatBytes';

/**
 * The application header: brand, live host metrics, mode controls, tab bar.
 *
 * Lifted out of App.tsx, where it was 290 lines of JSX in the middle of the
 * boot sequence and the pairing flow. It is presentational — every piece of
 * state it shows is owned by App and every button calls back up.
 */

export interface Bandwidth {
  rx_speed: number;
  tx_speed: number;
  rx_percent: number;
  tx_percent: number;
  cpu_usage: number;
  ram_usage: number;
}

export interface AppHeaderProps {
  appVersion: string;
  activeTab: Tab;
  onSelectTab: (tab: Tab) => void;
  theme: 'dark' | 'light';
  onToggleTheme: () => void;

  isKiosk: boolean;
  isAuthenticated: boolean | null;
  currentUser: any;
  onLogout: () => void;
  onOpenProfile: () => void;
  timezone: string;

  bandwidth: Bandwidth | null;

  /** Kiosk-only controls; ignored when isKiosk is false. */
  restoreMode: 'offline' | 'online';
  onToggleRestoreMode: () => void;
  onOpenPairing: () => void;
  onOpenNetwork: () => void;
  onExitKiosk: () => void;
  networkStatus: any;
  orchestratorReachable: boolean | null;
}

/** Green below half, amber past 50%, and red past 80%. */
const getUsageColorClass = (percent: number): string => {
  if (percent >= 80) return 'text-rose-400 font-bold';
  if (percent >= 50) return 'text-amber-400 font-semibold';
  return 'text-emerald-400';
};

export default function AppHeader({
  appVersion,
  activeTab,
  onSelectTab,
  theme,
  onToggleTheme,
  isKiosk,
  isAuthenticated,
  currentUser,
  onLogout,
  onOpenProfile,
  timezone,
  bandwidth,
  restoreMode,
  onToggleRestoreMode,
  onOpenPairing,
  onOpenNetwork,
  onExitKiosk,
  networkStatus,
  orchestratorReachable,
}: AppHeaderProps) {
  const { t } = useTranslation();
  const [profileOpen, setProfileOpen] = useState(false);
  const profileRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (profileRef.current && !profileRef.current.contains(event.target as Node)) {
        setProfileOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const showProfile = !isKiosk && isAuthenticated && currentUser;
  const visibleTabs = TABS.filter(tab => !isKiosk || tab.kioskVisible);

  return (
    <header className="bg-zinc-900 border-b border-zinc-800 sticky top-0 z-40">
      <div className="max-w-7xl mx-auto px-6 py-2.5 space-y-2.5">
        {/* Row 1: Logo/Title | Bandwidth | Actions */}
        <div className="flex flex-col md:flex-row items-center justify-between gap-3">
          {/* Left: Brand Identity with SVG logo */}
          <div className="flex-1 flex items-center gap-2.5 justify-center md:justify-start">
            <div className="relative p-1.5 bg-indigo-600/15 border border-indigo-500/30 rounded-lg shadow-sm flex items-center justify-center w-9 h-9">
              <svg className="w-5 h-5 text-indigo-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
              </svg>
              <span className="absolute top-1 right-1 w-1.5 h-1.5 bg-emerald-500 rounded-full shadow-[0_0_4px_rgba(16,185,129,0.8)]"></span>
            </div>
            <div>
              <h1 className="text-sm font-bold text-zinc-50 tracking-tight leading-none flex items-center gap-1.5">
                <span className="bg-indigo-500/10 text-indigo-300 border border-indigo-500/20 px-2 py-0.5 rounded font-mono font-bold text-[11px] uppercase tracking-wider">Edge-B.R.O.</span>
                <span className="text-[10px] bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 px-1.5 py-0.5 rounded font-mono font-bold">{appVersion}</span>
              </h1>
              <p className="text-[9px] text-zinc-500 font-semibold mt-1 uppercase tracking-wider">
                {t('appSubtitle')}
              </p>
            </div>
          </div>

          {/* Center: Server Metrics Widget — admin-only, orchestrator mode */}
          {!isKiosk && isAuthenticated && bandwidth && (
            <div className="flex-shrink-0 flex items-center gap-2.5 bg-zinc-950/40 border border-zinc-800/60 rounded-xl px-2.5 py-1 shadow-inner">
              <div className="flex items-center gap-1" title="CPU Utilization">
                <span className="text-[9px] text-zinc-500 uppercase tracking-wider font-bold font-mono">CPU</span>
                <span className={`text-[10px] font-mono font-semibold ${getUsageColorClass(bandwidth.cpu_usage)}`}>
                  {bandwidth.cpu_usage.toFixed(0)}%
                </span>
              </div>
              <div className="w-px h-2.5 bg-zinc-800" />

              <div className="flex items-center gap-1" title="RAM Utilization">
                <span className="text-[9px] text-zinc-500 uppercase tracking-wider font-bold font-mono">RAM</span>
                <span className={`text-[10px] font-mono font-semibold ${getUsageColorClass(bandwidth.ram_usage)}`}>
                  {bandwidth.ram_usage.toFixed(0)}%
                </span>
              </div>
              <div className="w-px h-2.5 bg-zinc-800" />

              <div className="flex items-center gap-1" title={t('bandwidthDownload')}>
                <ArrowDown size={11} className={bandwidth.rx_speed > 1024 ? getUsageColorClass(bandwidth.rx_percent) : 'text-zinc-600'} />
                <span className="text-[9px] text-zinc-500 uppercase tracking-wider font-bold font-mono">RX</span>
                <span className={`text-[10px] font-mono font-semibold ${getUsageColorClass(bandwidth.rx_percent)}`}>
                  {formatBytesPerSecond(bandwidth.rx_speed)}
                </span>
                <span className={`text-[8.5px] font-mono ${getUsageColorClass(bandwidth.rx_percent)}`}>({bandwidth.rx_percent.toFixed(1)}%)</span>
              </div>
              <div className="w-px h-2.5 bg-zinc-800" />

              <div className="flex items-center gap-1" title={t('bandwidthUpload')}>
                <ArrowUp size={11} className={bandwidth.tx_speed > 1024 ? getUsageColorClass(bandwidth.tx_percent) : 'text-zinc-600'} />
                <span className="text-[9px] text-zinc-500 uppercase tracking-wider font-bold font-mono">TX</span>
                <span className={`text-[10px] font-mono font-semibold ${getUsageColorClass(bandwidth.tx_percent)}`}>
                  {formatBytesPerSecond(bandwidth.tx_speed)}
                </span>
                <span className={`text-[8.5px] font-mono ${getUsageColorClass(bandwidth.tx_percent)}`}>({bandwidth.tx_percent.toFixed(1)}%)</span>
              </div>
            </div>
          )}

          {/* Right: Actions + Custom Language Switcher Dropdown */}
          <div className="flex-1 flex flex-wrap items-center justify-center md:justify-end gap-2">
            {isKiosk && (
              <>
                <div className="flex items-center bg-zinc-950 p-1 rounded-xl border border-zinc-800/80 shadow-inner">
                  <button
                    onClick={() => restoreMode !== 'online' && onToggleRestoreMode()}
                    className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-bold transition-colors duration-150 cursor-pointer ${
                      restoreMode === 'online'
                        ? 'bg-gradient-to-r from-emerald-500 to-teal-600 text-white shadow-md shadow-emerald-950/50'
                        : 'text-zinc-500 hover:text-zinc-400'
                    }`}
                  >
                    <Globe2 size={12} className={restoreMode === 'online' ? 'text-emerald-300' : ''} />
                    <span>{t('modeOnline')}</span>
                  </button>
                  <button
                    onClick={() => restoreMode !== 'offline' && onToggleRestoreMode()}
                    className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-bold transition-colors duration-150 cursor-pointer ${
                      restoreMode === 'offline'
                        ? 'bg-gradient-to-r from-amber-500 to-orange-600 text-white shadow-md shadow-amber-950/50'
                        : 'text-zinc-500 hover:text-zinc-400'
                    }`}
                  >
                    <HardDrive size={12} />
                    <span>{t('modeOffline')}</span>
                  </button>
                </div>

                {/* Pairing only means anything when the kiosk is meant to reach
                    a server; in offline mode there is nothing to pair with. */}
                {restoreMode === 'online' && (
                  <button
                    onClick={onOpenPairing}
                    className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-indigo-950/40 hover:bg-indigo-950/60 border border-indigo-900/30 hover:border-indigo-900/60 text-[11px] text-indigo-400 font-bold transition-colors duration-150 cursor-pointer"
                    title="Link to Orchestrator Server"
                  >
                    <Link2 size={12} className="text-indigo-400" />
                    <span>{t('linkServerButton') || 'Pair Server'}</span>
                  </button>
                )}
                <button
                  onClick={onOpenNetwork}
                  className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-zinc-950 hover:bg-zinc-900 border border-zinc-800 text-[11px] text-zinc-300 font-bold transition-all duration-200 cursor-pointer"
                >
                  {networkStatus?.wired?.connected ? (
                    <>
                      <Globe2 size={12} className="text-emerald-400" />
                      <span>{t('wiredLink')}</span>
                    </>
                  ) : networkStatus?.wifi?.connected ? (
                    <>
                      <Wifi size={12} className="text-emerald-400" />
                      <span>{networkStatus.wifi.ssid}</span>
                    </>
                  ) : (
                    <>
                      <Globe2 size={12} className="text-rose-400" />
                      <span className="text-rose-400 font-bold">{t('offline')}</span>
                    </>
                  )}
                </button>
                {/* Distinct from "offline": the link is up and the orchestrator
                    still is not answering, which points at the server rather
                    than at the cable in front of the technician. */}
                {(networkStatus?.wired?.connected || networkStatus?.wifi?.connected) && orchestratorReachable === false && (
                  <span className="flex items-center gap-1 text-[10px] text-amber-400 font-bold bg-amber-500/10 border border-amber-500/20 px-2 py-0.5 rounded-full animate-fade-in">
                    <AlertTriangle size={10} />
                    {t('serverUnreachable')}
                  </span>
                )}
                <button
                  onClick={onExitKiosk}
                  className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-red-950/20 hover:bg-red-950/40 border border-red-900/30 hover:border-red-900/60 text-[11px] text-red-400 font-bold transition-all duration-200 cursor-pointer"
                  title="Exit Kiosk Mode"
                >
                  <LogOut size={12} />
                  <span>{t('exitKiosk')}</span>
                </button>
              </>
            )}

            {/* Language Dropdown Selector */}
            <div className="flex items-center gap-2">
              {showProfile && (
                <div className="mr-0.5">
                  <NotificationBell timezone={timezone} />
                </div>
              )}
              {showProfile && (
                <div className="relative mr-0.5" ref={profileRef}>
                  <button
                    onClick={() => setProfileOpen(!profileOpen)}
                    className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-zinc-950 hover:bg-zinc-900 border border-zinc-800 text-[11px] text-zinc-300 font-bold transition-all duration-200 cursor-pointer outline-none"
                  >
                    <User size={12} className="text-zinc-400" />
                    <span>{currentUser.name || currentUser.username}</span>
                    <svg className={`w-2.5 h-2.5 text-zinc-500 transition-transform duration-200 ${profileOpen ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="3">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                    </svg>
                  </button>
                  {profileOpen && (
                    <div className="absolute right-0 mt-1.5 w-40 rounded-lg bg-zinc-900 border border-zinc-800 shadow-2xl p-1 z-50 origin-top-right animate-dropdown-in">
                      <button
                        onClick={() => {
                          setProfileOpen(false);
                          onOpenProfile();
                        }}
                        className="w-full text-left px-2.5 py-1.5 text-[11px] font-semibold rounded-md text-zinc-300 hover:text-zinc-50 hover:bg-zinc-800 transition-colors cursor-pointer"
                      >
                        {t('editProfile') || 'Edit Profile'}
                      </button>
                      <button
                        onClick={onLogout}
                        className="w-full text-left px-2.5 py-1.5 text-[11px] font-semibold rounded-md text-rose-400 hover:bg-rose-950/20 transition-colors border-t border-zinc-800 mt-1 pt-1.5 cursor-pointer"
                      >
                        {t('logoutButton') || 'Logout'}
                      </button>
                    </div>
                  )}
                </div>
              )}
              <LanguageSelector />
              <button
                onClick={onToggleTheme}
                className="p-1 bg-zinc-950 hover:bg-zinc-900 border border-zinc-800 rounded-lg text-zinc-400 hover:text-zinc-200 transition-all cursor-pointer flex items-center justify-center w-7 h-7"
                title={theme === 'dark' ? t('switchToLightMode') : t('switchToDarkMode')}
              >
                {theme === 'dark' ? <Sun size={13} /> : <Moon size={13} />}
              </button>
            </div>
          </div>
        </div>

        {/* Row 2: Tab Navigation Buttons */}
        <div className="border-t border-zinc-800/60 pt-1.5 flex justify-center w-full">
          <nav className="w-full flex flex-wrap items-center justify-center gap-1 bg-zinc-950 p-1 rounded-xl border border-zinc-800/60">
            {visibleTabs.map(({ id, icon: Icon, labelKey, labelFallback, accent }) => {
              const active = activeTab === id;
              const className = accent
                ? `flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-[11px] font-bold transition-all border ${
                    active
                      ? 'bg-indigo-600 text-white shadow-sm border-indigo-500 hover:bg-indigo-500'
                      : 'bg-indigo-600/10 hover:bg-indigo-600/20 text-indigo-400 dark:text-indigo-300 border-indigo-500/30'
                  }`
                : `flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-[11px] font-bold transition-all ${
                    active
                      ? 'bg-zinc-900 text-zinc-100 shadow-sm border border-zinc-800'
                      : 'text-zinc-400 hover:text-zinc-100'
                  }`;
              const iconClass = accent && active
                ? 'text-white'
                : accent
                  ? 'text-indigo-400 dark:text-indigo-300'
                  : 'text-indigo-400';

              return (
                <button key={id} onClick={() => onSelectTab(id)} className={className}>
                  <Icon size={13} className={iconClass} />
                  <span>{t(labelKey as any) || labelFallback}</span>
                </button>
              );
            })}
          </nav>
        </div>
      </div>
    </header>
  );
}
