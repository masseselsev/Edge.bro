import React, { useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { X, Play, Pause, Edit, Cpu, HardDrive, Cpu as MemIcon, Info, RefreshCw, Save, Database, History, Terminal, Calendar, Upload, ChevronDown, ChevronUp } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';
import type { Language } from '../i18n';
import NodeConsoleLogs from './NodeConsoleLogs';
import NodeBackupHistory from './NodeBackupHistory';
import { SearchableSelect } from './SearchableSelect';
import { InfoLabel } from './InfoLabel';
import { natChoiceFrom, natChoiceToValue } from './BackupGroupModal';
import { SmartBadge, ThermalBadge } from './NodeHealthBadges';
import type { NodeHealth } from './NodeHealthBadges';
import NodeHealthModal from './NodeHealthModal';
import type { NatChoice } from './BackupGroupModal';
import { kibToMbit, mbitToKib, parseMbitInput, formatMbit } from './rateLimit';

interface Node {
  id: number;
  hostname: string;
  ip_address: string;
  ssh_port: number;
  status: string;
  last_backup: string | null;
  disk_type: string;
  network_iface: string | null;
  efi_uuid: string | null;
  partition_layout: any[] | null;
  os_version: string | null;
  group_id: number | null;
  backup_paused: boolean;
  backup_today: boolean;
  missed_window: boolean;
  cpu_info: string | null;
  memory_info: string | null;
  edge_version: string | null;
  notes: string | null;
  hasp_runtime_version: string | null;
  is_backup_running?: boolean;
  backup_progress?: number;
  backup_task_id?: string | null;
  last_ping_status?: boolean | null;
  last_available_at?: string | null;
  // null = inherit from the node's group, then the global setting
  orchestrator_behind_nat?: boolean | null;
  // KiB/s. null = inherit the group limit, then unlimited.
  upload_rate_limit?: number | null;
}

interface BackupHistory {
  id: number;
  archive_name: string;
  timestamp: string;
  original_size: number;
  deduplicated_size: number;
  status: string;
  comment: string | null;
}

interface BackupGroup {
  id: number;
  name: string;
  orchestrator_behind_nat?: boolean | null;
  upload_rate_limit?: number | null;
}

interface TaskLog {
  id: string;
  task_type: string;
  status: string;
  created_at: string;
  log_output: string;
}

interface NodeDetailsModalProps {
  nodeId: number;
  onClose: () => void;
  onRefreshList: () => void;
}

export default function NodeDetailsModal({ nodeId, onClose, onRefreshList }: NodeDetailsModalProps) {
  const { t, language } = useTranslation();
  
  const [node, setNode] = useState<Node | null>(null);
  // Health is fetched separately from the node itself: it reads several
  // monitoring tables and must not delay the modal opening.
  const [health, setHealth] = useState<NodeHealth | null>(null);
  const [healthLoading, setHealthLoading] = useState(true);
  const [healthTab, setHealthTab] = useState<'smart' | 'thermal' | null>(null);
  const [history, setHistory] = useState<BackupHistory[]>([]);
  const [groups, setGroups] = useState<BackupGroup[]>([]);
  const [notes, setNotes] = useState('');
  const [groupId, setGroupId] = useState<number>(0);
  const [natChoice, setNatChoice] = useState<NatChoice>('inherit');
  const [rateLimit, setRateLimit] = useState<string>('');
  const [globalBehindNat, setGlobalBehindNat] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(false);
  const [savingNotes, setSavingNotes] = useState(false);
  const [triggeringAction, setTriggeringAction] = useState(false);

  const [activeTab, setActiveTab] = useState<'info' | 'logs'>('info');
  const [taskLogs, setTaskLogs] = useState<TaskLog[]>([]);
  const [selectedLogId, setSelectedLogId] = useState<string>('');
  const [haspStatus, setHaspStatus] = useState<{
    status: string;
    features: any[];
  } | null>(null);
  const [selectedLicenseFile, setSelectedLicenseFile] = useState<File | null>(null);
  const [applyingLicense, setApplyingLicense] = useState(false);
  const [licenseMessage, setLicenseMessage] = useState<{ text: string; isError: boolean } | null>(null);
  const [sentinelExpanded, setSentinelExpanded] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const fetchNodeDetails = async () => {
    setLoading(true);
    setLoadError(null);
    try {
      // Fetch this one node directly. This used to pull the paginated node
      // list and search it, which meant any node past the first page of 50
      // was simply not in the response and the modal hung on its spinner.
      const [nRes, hRes, gRes, tlRes, haspRes, sRes] = await Promise.all([
        fetch(`/api/nodes/${nodeId}`),
        fetch(`/api/nodes/${nodeId}/history`),
        fetch('/api/groups'),
        fetch(`/api/nodes/${nodeId}/task-logs`),
        fetch(`/api/nodes/${nodeId}/hasp-status`),
        fetch('/api/settings')
      ]);

      if (nRes.ok) {
        const found: Node = await nRes.json();
        setNode(found);
        setNotes(found.notes || '');
        setGroupId(found.group_id || 0);
        setNatChoice(natChoiceFrom(found.orchestrator_behind_nat));
        setRateLimit(found.upload_rate_limit == null ? '' : formatMbit(kibToMbit(found.upload_rate_limit)));
        if (found.status === 'RESTORED') {
          setSentinelExpanded(true);
          setTimeout(() => {
            const el = document.getElementById('sentinel-licensing-section');
            if (el) {
              el.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
          }, 150);
        }
      } else {
        setLoadError(
          nRes.status === 404
            ? t('nodeDetailsNotFound')
            : t('nodeDetailsLoadFailed')
        );
      }

      if (hRes.ok) {
        const histData = await hRes.json();
        if (Array.isArray(histData)) {
          histData.sort((a: any, b: any) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
          setHistory(histData);
        } else {
          setHistory([]);
        }
      }
      
      if (gRes.ok) {
        const gData = await gRes.json();
        setGroups(Array.isArray(gData) ? gData : []);
      }

      if (tlRes.ok) {
        const logsData = await tlRes.json();
        if (Array.isArray(logsData)) {
          setTaskLogs(logsData);
          if (logsData.length > 0) {
            setSelectedLogId(logsData[0].id);
          }
        } else {
          setTaskLogs([]);
        }
      }

      if (haspRes && haspRes.ok) {
        const haspData = await haspRes.json();
        setHaspStatus(haspData);
      }

      // Only used to spell out what "Inherit" currently resolves to; a user
      // without access to settings simply sees the plain label.
      if (sRes.ok) {
        const sData = await sRes.json();
        setGlobalBehindNat(!!sData.orchestrator_behind_nat);
      }
    } catch (err) {
      console.error("Failed to load node details:", err);
    } finally {
      setLoading(false);
    }
  };

  const handleApplyLicense = async () => {
    if (!selectedLicenseFile) return;
    setApplyingLicense(true);
    setLicenseMessage(null);
    try {
      const formData = new FormData();
      formData.append('file', selectedLicenseFile);
      
      const res = await fetch(`/api/nodes/${nodeId}/hasp-license`, {
        method: 'POST',
        body: formData
      });
      
      const data = await res.json();
      if (res.ok) {
        setLicenseMessage({ text: "License applied successfully!", isError: false });
        setSelectedLicenseFile(null);
        fetchNodeDetails();
      } else {
        setLicenseMessage({ text: data.detail || "Failed to apply license file.", isError: true });
      }
    } catch (err: any) {
      console.error(err);
      setLicenseMessage({ text: "Error uploading license: " + err.message, isError: true });
    } finally {
      setApplyingLicense(false);
    }
  };

  useEffect(() => {
    fetchNodeDetails();
  }, [nodeId]);

  // Escape closes the modal from any state, including while it is still
  // loading or showing a load error.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  // Kept out of fetchNodeDetails deliberately: the health endpoint reads
  // several monitoring tables and runs the cohort comparison across the
  // fleet, so making the modal wait on it would slow every open.
  const fetchHealth = async () => {
    setHealthLoading(true);
    try {
      const res = await fetch(`/api/monitoring/nodes/${nodeId}`);
      setHealth(res.ok ? await res.json() : null);
    } catch (e) {
      console.error(e);
      setHealth(null);
    } finally {
      setHealthLoading(false);
    }
  };

  useEffect(() => {
    fetchHealth();
  }, [nodeId]);

  const handleSaveNotes = async () => {
    setSavingNotes(true);
    try {
      const res = await fetch(`/api/nodes/${nodeId}/notes`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ notes })
      });
      if (res.ok) {
        onRefreshList();
      }
    } catch (err) {
      console.error(err);
    } finally {
      setSavingNotes(false);
    }
  };

  const handleNatOverride = async (choice: NatChoice) => {
    const previous = natChoice;
    setNatChoice(choice);
    try {
      const res = await fetch(`/api/nodes/${nodeId}/nat-override`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ orchestrator_behind_nat: natChoiceToValue(choice) })
      });
      if (res.ok) {
        onRefreshList();
        fetchNodeDetails();
      } else {
        setNatChoice(previous);
      }
    } catch (err) {
      console.error(err);
      setNatChoice(previous);
    }
  };

  const handleRateLimit = async () => {
    const trimmed = rateLimit.trim();
    let parsed: number | null;
    if (trimmed === '') {
      parsed = null;
    } else {
      const mbit = parseMbitInput(trimmed);
      if (mbit === null) {
        setRateLimit(node?.upload_rate_limit == null ? '' : formatMbit(kibToMbit(node.upload_rate_limit)));
        return;
      }
      parsed = mbitToKib(mbit);
    }
    if ((node?.upload_rate_limit ?? null) === parsed) return;
    try {
      const res = await fetch(`/api/nodes/${nodeId}/rate-limit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ upload_rate_limit: parsed })
      });
      if (res.ok) {
        onRefreshList();
        fetchNodeDetails();
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleGroupAssign = async (gid: number) => {
    setGroupId(gid);
    try {
      const res = await fetch(`/api/nodes/${nodeId}/assign-group/${gid}`, { method: 'POST' });
      if (res.ok) {
        onRefreshList();
        fetchNodeDetails();
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleTogglePause = async () => {
    setTriggeringAction(true);
    try {
      const res = await fetch(`/api/nodes/${nodeId}/toggle-pause`, { method: 'POST' });
      if (res.ok) {
        onRefreshList();
        fetchNodeDetails();
      }
    } catch (err) {
      console.error(err);
    } finally {
      setTriggeringAction(false);
    }
  };

  const handleBackupToday = async () => {
    setTriggeringAction(true);
    try {
      const res = await fetch(`/api/nodes/${nodeId}/backup-today`, { method: 'POST' });
      if (res.ok) {
        alert(t('backupToday') + ": Queued for next window.");
        onRefreshList();
        fetchNodeDetails();
      }
    } catch (err) {
      console.error(err);
    } finally {
      setTriggeringAction(false);
    }
  };

  const handleProvision = async () => {
    if (!window.confirm(t('reprovisionSubmit') + "?")) return;
    setTriggeringAction(true);
    try {
      const pass = window.prompt(t('bootstrapPassLabel'));
      if (!pass) return;
      const res = await fetch(`/api/nodes/${nodeId}/provision`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bootstrap_user: 'root', bootstrap_password: pass })
      });
      if (res.ok) {
        onClose();
        onRefreshList();
      }
    } catch (err) {
      console.error(err);
    } finally {
      setTriggeringAction(false);
    }
  };

  // Every pre-load state is dismissible. This branch used to render a bare
  // spinner with no close button, no backdrop handler and no Escape key, so
  // whenever the node could not be loaded the only way out was a page reload.
  if (!node) {
    return createPortal(
      <div
        className="fixed inset-0 bg-zinc-950/80 backdrop-blur-sm z-50 flex items-center justify-center p-4 animate-fade-in"
        onClick={onClose}
      >
        <div
          className="bg-zinc-900 border border-zinc-800 rounded-2xl p-8 shadow-2xl min-w-[320px]"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-start justify-between gap-6">
            {loadError ? (
              <div className="flex items-center gap-3">
                <Info className="h-6 w-6 text-rose-400 shrink-0" />
                <span className="text-zinc-200 text-sm">{loadError}</span>
              </div>
            ) : (
              <div className="flex items-center gap-3">
                <RefreshCw className="h-6 w-6 text-indigo-400 animate-spin" />
                <span className="text-zinc-200 text-sm">{t('nodeDetailsLoading')}</span>
              </div>
            )}
            <button
              onClick={onClose}
              aria-label={t('close')}
              className="text-zinc-400 hover:text-zinc-200 p-1 rounded-md hover:bg-zinc-800 transition shrink-0"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>
      </div>,
      document.body
    );
  }

  return createPortal(
    <div className="fixed inset-0 bg-zinc-950/85 backdrop-blur-sm z-50 flex items-center justify-center p-4 overflow-y-auto animate-fade-in">
      <div className="bg-zinc-900 border border-zinc-800 rounded-2xl w-full max-w-6xl max-h-[92dvh] my-auto shadow-2xl flex flex-col overflow-hidden animate-modal-in">
        {/* Header */}
        <div className="flex justify-between items-center p-5 border-b border-zinc-800">
          <div>
            <h3 className="text-xl font-bold text-zinc-100 flex items-center gap-2">
              <Database className="h-5 w-5 text-indigo-400" />
              {node.hostname}
            </h3>
            <p className="text-xs text-zinc-400 font-mono mt-0.5">{node.ip_address}:{node.ssh_port}</p>
          </div>
          <button onClick={onClose} className="text-zinc-400 hover:text-zinc-200 p-1 rounded-md hover:bg-zinc-800 transition">
            <X className="h-6 w-6" />
          </button>
        </div>

        {/* Modal body (scrollable) */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* Tab Navigation Switches */}
          <div className="flex gap-4 border-b border-zinc-800 pb-3 mb-4 font-sans text-xs">
            <button
              onClick={() => setActiveTab('info')}
              className={`pb-2 px-1 font-bold transition-all cursor-pointer outline-none ${activeTab === 'info' ? 'text-indigo-400 border-b-2 border-indigo-400' : 'text-zinc-400 hover:text-zinc-200'}`}
            >
              {t('tabSystemInfoSettings')}
            </button>
            <button
              onClick={() => setActiveTab('logs')}
              className={`pb-2 px-1 font-bold transition-all cursor-pointer outline-none ${activeTab === 'logs' ? 'text-indigo-400 border-b-2 border-indigo-400' : 'text-zinc-400 hover:text-zinc-200'}`}
            >
              {t('tabConsoleLogs')}
            </button>
          </div>

          {activeTab === 'info' && (
            <>
              {/* Hardware Specs Cards */}
              <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-5 gap-4">
            <div className="relative bg-zinc-950/40 border border-zinc-800/80 rounded-lg p-3.5 pt-6 flex items-center gap-3">
              <div className="absolute top-2 right-2">
                <ThermalBadge
                  thermal={health?.thermal}
                  loading={healthLoading}
                  onClick={() => setHealthTab('thermal')}
                />
              </div>
              <Cpu className="h-8 w-8 text-cyan-400/90 shrink-0" />
              <div className="min-w-0">
                <span className="text-[10px] uppercase font-bold text-zinc-500 block">{t('cpu')}</span>
                <span className="text-xs font-semibold text-zinc-200 block mt-0.5 truncate" title={node.cpu_info || 'UNKNOWN'}>
                  {node.cpu_info || 'Generic CPU'}
                </span>
              </div>
            </div>

            <div className="bg-zinc-950/40 border border-zinc-800/80 rounded-lg p-3.5 flex items-center gap-3">
              <MemIcon className="h-8 w-8 text-purple-400/90" />
              <div>
                <span className="text-[10px] uppercase font-bold text-zinc-500 block">{t('memory')}</span>
                <span className="text-xs font-semibold text-zinc-200 block mt-0.5">
                  {node.memory_info || 'Unknown RAM'}
                </span>
              </div>
            </div>

            <div className="relative bg-zinc-950/40 border border-zinc-800/80 rounded-lg p-3.5 pt-6 flex items-center gap-3">
              <div className="absolute top-2 right-2">
                <SmartBadge
                  smart={health?.smart?.[0]}
                  loading={healthLoading}
                  onClick={() => setHealthTab('smart')}
                />
              </div>
              <HardDrive className="h-8 w-8 text-amber-400/90 shrink-0" />
              <div className="min-w-0">
                <span className="text-[10px] uppercase font-bold text-zinc-500 block">{t('diskDrive') || 'Disk Drive'}</span>
                <span className="text-xs font-semibold text-zinc-200 block mt-0.5 truncate">
                  {node.disk_type}
                </span>
              </div>
            </div>

            <div className="bg-zinc-950/40 border border-zinc-800/80 rounded-lg p-3.5 flex items-center gap-3">
              <Info className="h-8 w-8 text-emerald-400/90" />
              <div>
                <span className="text-[10px] uppercase font-bold text-zinc-500 block">{t('edgeVersion')}</span>
                <span className="text-xs font-semibold text-zinc-200 block mt-0.5">
                  {node.edge_version || 'UNKNOWN'}
                </span>
              </div>
            </div>

            <div className="bg-zinc-950/40 border border-zinc-800/80 rounded-lg p-3.5 flex items-center gap-3">
              <Database className="h-8 w-8 text-indigo-400/90" />
              <div>
                <span className="text-[10px] uppercase font-bold text-zinc-500 block">{t('sentinelRuntimeVersion') || 'Sentinel Runtime'}</span>
                <span className="text-xs font-semibold text-zinc-200 block mt-0.5" title={node.hasp_runtime_version || 'None'}>
                  {node.hasp_runtime_version || 'None'}
                </span>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Scheduling and actions */}
            <div className="lg:col-span-2 space-y-4">
              <div className="bg-zinc-950/30 border border-zinc-800/80 rounded-xl p-5 space-y-4">
                <h4 className="font-bold text-zinc-200 text-sm border-b border-zinc-800 pb-2 flex items-center gap-1.5">
                  <Calendar className="h-4.5 w-4.5 text-indigo-400" />
                  Scheduler Configurations
                </h4>

                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs font-semibold text-zinc-400 mb-1.5">
                      {t('backupGroup')}
                    </label>
                    <SearchableSelect
                      options={[
                        { value: 0, label: t('noGroup') },
                        ...groups.map(g => ({ value: g.id, label: g.name }))
                      ]}
                      value={groupId}
                      onChange={(val) => handleGroupAssign(Number(val))}
                      placeholder={t('backupGroup')}
                    />
                  </div>

                  <div className="space-y-1.5">
                    <span className="block text-xs font-semibold text-zinc-400">{t('statusTags') || 'Status Tags'}</span>
                    <div className="flex gap-2.5">
                      {node.backup_paused ? (
                        <span className="px-2 py-1 bg-amber-500/10 text-amber-400 border border-amber-500/20 rounded-md text-xs font-semibold">
                          {t('backupPaused')}
                        </span>
                      ) : (
                        <span className="px-2 py-1 bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 rounded-md text-xs font-semibold">
                          {t('active')}
                        </span>
                      )}
                      {node.missed_window && (
                        <span className="px-2 py-1 bg-rose-500/10 text-rose-400 border border-rose-500/20 rounded-md text-xs font-semibold animate-pulse">
                          {t('missedWindow')}
                        </span>
                      )}
                      {node.backup_today && (
                        <span className="px-2 py-1 bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 rounded-md text-xs font-semibold">
                          {t('backupToday')} (Queued)
                        </span>
                      )}
                    </div>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <InfoLabel
                      label={t('natOverrideLabel')}
                      hint={t('natOverrideNodeHint')}
                      className="block text-xs font-semibold text-zinc-400 mb-1.5"
                    />
                    <SearchableSelect
                      options={[
                        {
                          value: 'inherit',
                          // Spelling out what inheriting resolves to right now
                          // saves a trip to the group and to Settings.
                          label: (() => {
                            const inherited =
                              groups.find(g => g.id === groupId)?.orchestrator_behind_nat ?? globalBehindNat;
                            if (inherited === null || inherited === undefined) return t('natOverrideInherit');
                            return `${t('natOverrideInherit')} (${inherited ? t('natEffectiveOn') : t('natEffectiveOff')})`;
                          })()
                        },
                        { value: 'nat', label: t('natOverrideOn') },
                        { value: 'direct', label: t('natOverrideOff') }
                      ]}
                      value={natChoice}
                      onChange={(val) => handleNatOverride(val as NatChoice)}
                      placeholder={t('natOverrideInherit')}
                    />
                  </div>
                  <div>
                    <InfoLabel
                      label={t('rateLimitLabel')}
                      hint={t('rateLimitNodeHint')}
                      className="block text-xs font-semibold text-zinc-400 mb-1.5"
                    />
                    <input
                      type="text"
                      inputMode="decimal"
                      value={rateLimit}
                      onChange={(e) => setRateLimit(e.target.value)}
                      onBlur={handleRateLimit}
                      onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
                      placeholder={(() => {
                        // Spell out what leaving this empty resolves to, the way
                        // the NAT selector above does.
                        const inherited = groups.find(g => g.id === groupId)?.upload_rate_limit;
                        return inherited
                          ? `${t('rateLimitInherit')} (${formatMbit(kibToMbit(inherited))} Mbit/s)`
                          : `${t('rateLimitInherit')} (${t('rateLimitUnlimited')})`;
                      })()}
                      className="w-full bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-indigo-500/50"
                    />
                  </div>
                </div>

                {/* Scheduler Commands bar */}
                <div className="flex flex-wrap gap-3 pt-3">
                  <button
                    onClick={handleTogglePause}
                    disabled={triggeringAction}
                    className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold transition ${
                      node.backup_paused
                        ? 'bg-emerald-600 hover:bg-emerald-700 text-white shadow-md shadow-emerald-900/10'
                        : 'bg-amber-600 hover:bg-amber-700 text-white shadow-md shadow-amber-900/10'
                    }`}
                  >
                    {node.backup_paused ? <Play className="h-4 w-4 fill-current" /> : <Pause className="h-4 w-4 fill-current" />}
                    {node.backup_paused ? t('resume') : t('pause')}
                  </button>

                  <button
                    onClick={handleBackupToday}
                    disabled={triggeringAction || node.backup_paused || node.backup_today}
                    className="flex items-center gap-1.5 px-4 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-zinc-800 disabled:text-zinc-500 text-white rounded-lg text-sm font-semibold transition shadow-md shadow-indigo-900/10"
                  >
                    <Calendar className="h-4 w-4" />
                    {t('backupToday')}
                  </button>

                  <button
                    onClick={handleProvision}
                    disabled={triggeringAction}
                    className="flex items-center gap-1.5 px-4 py-2 bg-zinc-800 hover:bg-zinc-750 text-zinc-200 border border-zinc-700/80 rounded-lg text-sm font-semibold transition hover:text-indigo-400"
                  >
                    <RefreshCw className="h-4 w-4" />
                    {t('reprovision')}
                  </button>
                </div>
              </div>
            </div>

            {/* Notes Section */}
            <div className="bg-zinc-950/30 border border-zinc-800/80 rounded-xl p-5 flex flex-col justify-between">
              <div className="space-y-3">
                <h4 className="font-bold text-zinc-200 text-sm border-b border-zinc-800 pb-2 flex items-center gap-1.5">
                  <Edit className="h-4.5 w-4.5 text-indigo-400" />
                  {t('notes')}
                </h4>
                <textarea
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder={t('notesPlaceholder')}
                  rows={4}
                  className="w-full p-3 bg-zinc-900 border border-zinc-800 rounded-lg text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-indigo-500 text-sm resize-none"
                />
              </div>
              <button
                onClick={handleSaveNotes}
                disabled={savingNotes}
                className="mt-3 w-full flex items-center justify-center gap-1.5 px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-sm font-semibold transition"
              >
                <Save className="h-4 w-4" />
                {savingNotes ? 'Saving...' : t('saveNotes')}
              </button>
            </div>
          </div>

          {(node.status === 'RESTORED' || (node.hasp_runtime_version && node.hasp_runtime_version !== 'None')) && (
            <div id="sentinel-licensing-section" className="bg-indigo-50/40 dark:bg-indigo-950/20 border border-indigo-200 dark:border-indigo-500/25 rounded-2xl p-5 space-y-3.5 shadow-lg shadow-indigo-950/10 animate-fade-in">
              <div 
                className="flex items-center justify-between border-b border-indigo-500/15 pb-3 cursor-pointer select-none hover:opacity-90 transition"
                onClick={() => setSentinelExpanded(!sentinelExpanded)}
              >
                <div className="flex items-center gap-2.5 text-indigo-500 dark:text-indigo-400 font-bold text-sm">
                  <Info className="h-5 w-5" />
                  <span>{t('sentinelLicensingLabel')}</span>
                </div>
                <div className="flex items-center gap-2">
                  {haspStatus && (
                    <span className={`px-2.5 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border ${
                      haspStatus.status === 'active' ? 'hasp-badge-active' :
                      haspStatus.status === 'expired' ? 'hasp-badge-expired' :
                      haspStatus.status === 'clone_detected' ? 'hasp-badge-danger animate-pulse' :
                      haspStatus.status === 'disabled' ? 'hasp-badge-danger' :
                      'hasp-badge-neutral'
                    }`}>
                      {haspStatus.status.replace('_', ' ')}
                    </span>
                  )}
                  {sentinelExpanded ? (
                    <ChevronUp className="h-4 w-4 text-zinc-500 dark:text-zinc-400" />
                  ) : (
                    <ChevronDown className="h-4 w-4 text-zinc-500 dark:text-zinc-400" />
                  )}
                </div>
              </div>

              {sentinelExpanded && (
                <>
                  {haspStatus && haspStatus.status === 'clone_detected' && (
                    <div className="p-3 bg-red-105 dark:bg-red-950/25 border border-red-200 dark:border-red-500/20 text-red-800 dark:text-red-400 text-xs rounded-xl flex items-start gap-2.5">
                      <span className="font-bold">{t('warningLabel')}:</span>
                      <span>{t('sentinelWarningClone')}</span>
                    </div>
                  )}

              {/* Fingerprint retrieval and download */}
              <div className="space-y-2">
                <p className="text-xs text-zinc-600 dark:text-zinc-400 leading-relaxed">
                  {t('downloadFingerprintHelp') || 'To activate the license, download the fingerprint (C2V) file from the node:'}
                </p>
                <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3">
                  <code className="flex-1 bg-zinc-100 dark:bg-zinc-950 px-3 py-2.5 rounded-lg text-xs text-zinc-800 dark:text-zinc-300 font-mono select-all border border-zinc-200 dark:border-zinc-850/80">
                    /var/hasplm/fingerprint
                  </code>
                  <a
                    href={`/api/nodes/${node.id}/hasp-fingerprint`}
                    download
                    className="inline-flex items-center justify-center gap-1.5 px-4 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-xs font-bold transition shadow-md shadow-indigo-900/15 cursor-pointer"
                  >
                    <Terminal className="h-4 w-4" />
                    {t('downloadC2vFile')}
                  </a>
                </div>
              </div>

              {/* Upload license update section */}
              <div className="pt-3 border-t border-indigo-500/10 space-y-2">
                <p className="text-xs text-zinc-600 dark:text-zinc-400 leading-relaxed font-semibold">
                  {t('applyLicenseTitle')}
                </p>
                <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3">
                  <input
                    type="file"
                    id="hasp-license-file"
                    accept=".v2c,.v2cp,.h2r,.r2h,.h2h"
                    className="hidden"
                    onChange={(e) => {
                      if (e.target.files && e.target.files[0]) {
                        setSelectedLicenseFile(e.target.files[0]);
                      }
                    }}
                  />
                  <div className="flex-1 flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => document.getElementById('hasp-license-file')?.click()}
                      className="px-3 py-2 bg-zinc-100 dark:bg-zinc-950 border border-zinc-250 dark:border-zinc-850/80 text-zinc-800 dark:text-zinc-300 rounded-lg text-xs font-semibold hover:bg-zinc-200 dark:hover:bg-zinc-900 transition flex items-center gap-1.5 shrink-0"
                    >
                      <Upload className="h-3.5 w-3.5 text-indigo-400" />
                      {t('chooseV2cFile')}
                    </button>
                    <span className="text-xs text-zinc-500 dark:text-zinc-400 truncate max-w-[180px]" title={selectedLicenseFile?.name || t('noFileSelected')}>
                      {selectedLicenseFile?.name || t('noFileSelected')}
                    </span>
                  </div>
                  <button
                    type="button"
                    disabled={!selectedLicenseFile || applyingLicense}
                    onClick={handleApplyLicense}
                    className="inline-flex items-center justify-center gap-1.5 px-4 py-2.5 bg-emerald-600 hover:bg-emerald-700 disabled:bg-zinc-300 dark:disabled:bg-zinc-800 disabled:text-zinc-500 text-white rounded-lg text-xs font-bold transition shadow-md shadow-emerald-900/15 cursor-pointer disabled:cursor-not-allowed"
                  >
                    {applyingLicense ? t('applyingLicense') : t('applyV2cButton')}
                  </button>
                </div>
              </div>

              {licenseMessage && (
                <div className={`p-2.5 rounded-lg text-xs font-semibold border ${
                  licenseMessage.isError 
                    ? 'bg-red-50 dark:bg-red-500/10 text-red-800 dark:text-red-400 border-red-200 dark:border-red-500/20' 
                    : 'bg-emerald-50 dark:bg-emerald-500/10 text-emerald-800 dark:text-emerald-400 border-emerald-200 dark:border-emerald-500/20'
                }`}>
                  {licenseMessage.text}
                </div>
              )}

              {/* Features List */}
              {haspStatus && haspStatus.features && haspStatus.features.length > 0 && (
                <div className="pt-2 border-t border-indigo-500/10 space-y-2">
                  <span className="text-[10px] uppercase font-bold text-zinc-500 dark:text-zinc-400 tracking-wider block">
                    {t('activeLicenseFeatures')} ({haspStatus.features.length})
                  </span>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-2 p-0.5 max-h-60 overflow-y-auto pr-1">
                    {haspStatus.features.map((feat: any) => (
                      <div 
                        key={feat.id} 
                        className={`p-2.5 rounded-lg text-xs flex items-center justify-between border ${
                          feat.unusable === '0' 
                            ? 'bg-emerald-50/40 dark:bg-emerald-950/10 border-emerald-100 dark:border-emerald-500/15 text-emerald-800 dark:text-emerald-300' 
                            : 'bg-zinc-50/50 dark:bg-zinc-900/20 border-zinc-200/60 dark:border-zinc-800/80 text-zinc-500 dark:text-zinc-400'
                        }`}
                      >
                        <div className="truncate pr-2">
                          <span className="font-bold block truncate">
                            {feat.name}
                          </span>
                          <span className="text-[10px] text-zinc-500 dark:text-zinc-400 block">
                            FID: {feat.id} • Product: {feat.product_name} ({feat.product_id})
                          </span>
                        </div>
                        <span className={`text-[10px] px-1.5 py-0.5 rounded font-mono font-bold shrink-0 ${
                          feat.unusable === '0' 
                            ? 'bg-emerald-100 dark:bg-emerald-500/15 text-emerald-800 dark:text-emerald-400' 
                            : 'bg-zinc-200 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-500'
                        }`}>
                          {feat.lic_type}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}

          {/* Backup History Datatable */}
          <NodeBackupHistory
            history={history}
            language={language}
            t={t}
          />
        </>
      )}

          {activeTab === 'logs' && (
            <NodeConsoleLogs
              taskLogs={taskLogs}
              selectedLogId={selectedLogId}
              setSelectedLogId={setSelectedLogId}
              language={language}
              t={t}
            />
          )}
        </div>
      </div>

      {healthTab && node && (
        <NodeHealthModal
          nodeId={nodeId}
          hostname={node.hostname}
          initialTab={healthTab}
          onClose={() => {
            setHealthTab(null);
            // The detail view can trigger a harvest, so re-read the badges on
            // the way out rather than leaving them showing the old verdict.
            fetchHealth();
          }}
        />
      )}
    </div>,
    document.body
  );
}
