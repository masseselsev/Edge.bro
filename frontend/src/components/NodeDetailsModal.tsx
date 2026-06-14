import React, { useState, useEffect } from 'react';
import { X, Play, Pause, Edit, Cpu, HardDrive, Cpu as MemIcon, Info, RefreshCw, Save, Database, History, Terminal, Calendar } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';
import type { Language } from '../i18n/translations';

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

  const fetchNodeDetails = async () => {
    setLoading(true);
    try {
      const [nRes, hRes, gRes] = await Promise.all([
        fetch('/api/nodes'),
        fetch(`/api/nodes/${nodeId}/history`),
        fetch('/api/groups')
      ]);

      if (nRes.ok) {
        const allNodes: Node[] = await nRes.json();
        const found = allNodes.find(n => n.id === nodeId);
        if (found) {
          setNode(found);
          setNotes(found.notes || '');
          setGroupId(found.group_id || 0);
        }
      }
      
      if (hRes.ok) {
        const histData = await hRes.json();
        histData.sort((a: any, b: any) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
        setHistory(histData);
      }
      
      if (gRes.ok) {
        setGroups(await gRes.json());
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
    return (
      <div className="fixed inset-0 bg-slate-950/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-8 flex items-center gap-3">
          <RefreshCw className="h-6 w-6 text-indigo-400 animate-spin" />
          <span className="text-slate-200">Loading node details...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 bg-slate-950/85 backdrop-blur-sm z-50 flex items-center justify-center p-4">
      <div className="bg-slate-900 border border-slate-800 rounded-xl w-full max-w-4xl max-h-[90vh] shadow-2xl flex flex-col overflow-hidden animate-modal-in">
        {/* Header */}
        <div className="flex justify-between items-center p-5 border-b border-slate-800">
          <div>
            <h3 className="text-xl font-bold text-slate-100 flex items-center gap-2">
              <Database className="h-5 w-5 text-indigo-400" />
              {node.hostname}
            </h3>
            <p className="text-xs text-slate-400 font-mono mt-0.5">{node.ip_address}:{node.ssh_port}</p>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-200 p-1 rounded-md hover:bg-slate-800 transition">
            <X className="h-6 w-6" />
          </button>
        </div>

        {/* Modal body (scrollable) */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* Hardware Specs Cards */}
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4">
            <div className="bg-slate-950/40 border border-slate-800/80 rounded-lg p-3.5 flex items-center gap-3">
              <Cpu className="h-8 w-8 text-cyan-400/90" />
              <div>
                <span className="text-[10px] uppercase font-bold text-slate-500 block">{t('cpu')}</span>
                <span className="text-sm font-semibold text-slate-200 truncate max-w-[150px] block" title={node.cpu_info || 'UNKNOWN'}>
                  {node.cpu_info || 'Generic CPU'}
                </span>
              </div>
            </div>

            <div className="bg-slate-950/40 border border-slate-800/80 rounded-lg p-3.5 flex items-center gap-3">
              <MemIcon className="h-8 w-8 text-purple-400/90" />
              <div>
                <span className="text-[10px] uppercase font-bold text-slate-500 block">{t('memory')}</span>
                <span className="text-sm font-semibold text-slate-200 block">
                  {node.memory_info || 'Unknown RAM'}
                </span>
              </div>
            </div>

            <div className="bg-slate-950/40 border border-slate-800/80 rounded-lg p-3.5 flex items-center gap-3">
              <HardDrive className="h-8 w-8 text-amber-400/90" />
              <div>
                <span className="text-[10px] uppercase font-bold text-slate-500 block">Disk Drive</span>
                <span className="text-sm font-semibold text-slate-200 block">
                  {node.disk_type}
                </span>
              </div>
            </div>

            <div className="bg-slate-950/40 border border-slate-800/80 rounded-lg p-3.5 flex items-center gap-3">
              <Info className="h-8 w-8 text-emerald-400/90" />
              <div>
                <span className="text-[10px] uppercase font-bold text-slate-500 block">{t('edgeVersion')}</span>
                <span className="text-sm font-semibold text-slate-200 block">
                  {node.edge_version || 'UNKNOWN'}
                </span>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Scheduling and actions */}
            <div className="lg:col-span-2 space-y-4">
              <div className="bg-slate-950/30 border border-slate-800/80 rounded-xl p-5 space-y-4">
                <h4 className="font-bold text-slate-200 text-sm border-b border-slate-800 pb-2 flex items-center gap-1.5">
                  <Calendar className="h-4.5 w-4.5 text-indigo-400" />
                  Scheduler Configurations
                </h4>

                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs font-semibold text-slate-400 mb-1.5">
                      {t('backupGroup')}
                    </label>
                    <select
                      value={groupId}
                      onChange={(e) => handleGroupAssign(Number(e.target.value))}
                      className="w-full px-3 py-1.5 bg-slate-900 border border-slate-800 rounded-lg text-slate-100 text-sm focus:outline-none focus:border-indigo-500"
                    >
                      <option value={0}>{t('noGroup')}</option>
                      {groups.map(g => (
                        <option key={g.id} value={g.id}>{g.name}</option>
                      ))}
                    </select>
                  </div>

                  <div className="space-y-1.5">
                    <span className="block text-xs font-semibold text-slate-400">Status Tags</span>
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
                    className="flex items-center gap-1.5 px-4 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-800 disabled:text-slate-500 text-white rounded-lg text-sm font-semibold transition shadow-md shadow-indigo-900/10"
                  >
                    <Calendar className="h-4 w-4" />
                    {t('backupToday')}
                  </button>

                  <button
                    onClick={handleProvision}
                    disabled={triggeringAction}
                    className="flex items-center gap-1.5 px-4 py-2 bg-slate-800 hover:bg-slate-750 text-slate-200 border border-slate-700/80 rounded-lg text-sm font-semibold transition hover:text-indigo-400"
                  >
                    <RefreshCw className="h-4 w-4" />
                    {t('reprovision')}
                  </button>
                </div>
              </div>
            </div>

            {/* Notes Section */}
            <div className="bg-slate-950/30 border border-slate-800/80 rounded-xl p-5 flex flex-col justify-between">
              <div className="space-y-3">
                <h4 className="font-bold text-slate-200 text-sm border-b border-slate-800 pb-2 flex items-center gap-1.5">
                  <Edit className="h-4.5 w-4.5 text-indigo-400" />
                  {t('notes')}
                </h4>
                <textarea
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder={t('notesPlaceholder')}
                  rows={4}
                  className="w-full p-3 bg-slate-900 border border-slate-800 rounded-lg text-slate-200 placeholder-slate-600 focus:outline-none focus:border-indigo-500 text-sm resize-none"
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
          <div className="bg-slate-950/30 border border-slate-800/80 rounded-xl p-5 space-y-4">
            <h4 className="font-bold text-slate-200 text-sm border-b border-slate-800 pb-2 flex items-center gap-1.5">
              <History className="h-4.5 w-4.5 text-indigo-400" />
              Backup History & Archives
            </h4>
            
            <div className="overflow-x-auto rounded-lg border border-slate-800">
              <table className="w-full text-left border-collapse text-sm">
                <thead>
                  <tr className="bg-slate-950 text-slate-400 font-semibold border-b border-slate-800">
                    <th className="p-3">Archive Name</th>
                    <th className="p-3">Date & Time (UTC)</th>
                    <th className="p-3">Original Size</th>
                    <th className="p-3">Deduplicated Size</th>
                    <th className="p-3">Status</th>
                    <th className="p-3">Comment</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((row) => (
                    <tr key={row.id} className="border-b border-slate-800/80 hover:bg-slate-850/30 text-slate-200">
                      <td className="p-3 font-mono text-xs">{row.archive_name}</td>
                      <td className="p-3 font-mono text-xs">
                        {new Date(row.timestamp).toLocaleString(language === 'ru' ? 'ru-RU' : language === 'uk' ? 'uk-UA' : 'en-US')}
                      </td>
                      <td className="p-3">{formatBytes(row.original_size)}</td>
                      <td className="p-3">{formatBytes(row.deduplicated_size)}</td>
                      <td className="p-3">
                        <span className={`px-2 py-0.5 rounded text-xs font-semibold ${
                          row.status === 'SUCCESS'
                            ? 'bg-emerald-500/10 text-emerald-400'
                            : 'bg-rose-500/10 text-rose-400'
                        }`}>
                          {row.status}
                        </span>
                      </td>
                      <td className="p-3 max-w-[200px] truncate text-slate-400" title={row.comment || ''}>
                        {row.comment || '-'}
                      </td>
                    </tr>
                  ))}
                  {history.length === 0 && (
                    <tr>
                      <td colSpan={6} className="p-6 text-center text-slate-500">
                        No backup snapshots executed yet.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
