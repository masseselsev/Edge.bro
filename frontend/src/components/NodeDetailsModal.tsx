import React, { useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { X, Play, Pause, Edit, Cpu, HardDrive, Cpu as MemIcon, Info, RefreshCw, Save, Database, History, Terminal, Calendar } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';
import type { Language } from '../i18n/translations';
import NodeConsoleLogs from './NodeConsoleLogs';
import NodeBackupHistory from './NodeBackupHistory';
import { SearchableSelect } from './SearchableSelect';

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
  const [history, setHistory] = useState<BackupHistory[]>([]);
  const [groups, setGroups] = useState<BackupGroup[]>([]);
  const [notes, setNotes] = useState('');
  const [groupId, setGroupId] = useState<number>(0);
  const [loading, setLoading] = useState(false);
  const [savingNotes, setSavingNotes] = useState(false);
  const [triggeringAction, setTriggeringAction] = useState(false);

  const [activeTab, setActiveTab] = useState<'info' | 'logs'>('info');
  const [taskLogs, setTaskLogs] = useState<TaskLog[]>([]);
  const [selectedLogId, setSelectedLogId] = useState<string>('');

  const fetchNodeDetails = async () => {
    setLoading(true);
    try {
      const [nRes, hRes, gRes, tlRes] = await Promise.all([
        fetch('/api/nodes'),
        fetch(`/api/nodes/${nodeId}/history`),
        fetch('/api/groups'),
        fetch(`/api/nodes/${nodeId}/task-logs`)
      ]);

      if (nRes.ok) {
        const allNodes = await nRes.json();
        if (Array.isArray(allNodes)) {
          const found = allNodes.find((n: Node) => n.id === nodeId);
          if (found) {
            setNode(found);
            setNotes(found.notes || '');
            setGroupId(found.group_id || 0);
          }
        }
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
    } catch (err) {
      console.error("Failed to load node details:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchNodeDetails();
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

  // Helper formatting bytes
  const formatBytes = (bytes: number) => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  if (!node) {
    return createPortal(
      <div className="fixed inset-0 bg-zinc-950/80 backdrop-blur-sm z-50 flex items-center justify-center p-4 animate-fade-in">
        <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-8 flex items-center gap-3">
          <RefreshCw className="h-6 w-6 text-indigo-400 animate-spin" />
          <span className="text-zinc-200">Loading node details...</span>
        </div>
      </div>,
      document.body
    );
  }

  return createPortal(
    <div className="fixed inset-0 bg-zinc-950/85 backdrop-blur-sm z-50 flex items-center justify-center p-4 animate-fade-in">
      <div className="bg-zinc-900 border border-zinc-800 rounded-2xl w-full max-w-4xl max-h-[90vh] shadow-2xl flex flex-col overflow-hidden animate-modal-in">
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
              <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4">
            <div className="bg-zinc-950/40 border border-zinc-800/80 rounded-lg p-3.5 flex items-center gap-3">
              <Cpu className="h-8 w-8 text-cyan-400/90" />
              <div>
                <span className="text-[10px] uppercase font-bold text-zinc-500 block">{t('cpu')}</span>
                <span className="text-sm font-semibold text-zinc-200 truncate max-w-[150px] block" title={node.cpu_info || 'UNKNOWN'}>
                  {node.cpu_info || 'Generic CPU'}
                </span>
              </div>
            </div>

            <div className="bg-zinc-950/40 border border-zinc-800/80 rounded-lg p-3.5 flex items-center gap-3">
              <MemIcon className="h-8 w-8 text-purple-400/90" />
              <div>
                <span className="text-[10px] uppercase font-bold text-zinc-500 block">{t('memory')}</span>
                <span className="text-sm font-semibold text-zinc-200 block">
                  {node.memory_info || 'Unknown RAM'}
                </span>
              </div>
            </div>

            <div className="bg-zinc-950/40 border border-zinc-800/80 rounded-lg p-3.5 flex items-center gap-3">
              <HardDrive className="h-8 w-8 text-amber-400/90" />
              <div>
                <span className="text-[10px] uppercase font-bold text-zinc-500 block">Disk Drive</span>
                <span className="text-sm font-semibold text-zinc-200 block">
                  {node.disk_type}
                </span>
              </div>
            </div>

            <div className="bg-zinc-950/40 border border-zinc-800/80 rounded-lg p-3.5 flex items-center gap-3">
              <Info className="h-8 w-8 text-emerald-400/90" />
              <div>
                <span className="text-[10px] uppercase font-bold text-zinc-500 block">{t('edgeVersion')}</span>
                <span className="text-sm font-semibold text-zinc-200 block">
                  {node.edge_version || 'UNKNOWN'}
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
                    <span className="block text-xs font-semibold text-zinc-400">Status Tags</span>
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

          {/* Backup History Datatable */}
          <NodeBackupHistory
            history={history}
            language={language}
            formatBytes={formatBytes}
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
    </div>,
    document.body
  );
}
