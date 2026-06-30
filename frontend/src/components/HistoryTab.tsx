import React, { useState, useEffect, useCallback } from 'react';
import { Database, TrendingDown, ArrowDownCircle, RefreshCw, Trash2, AlertTriangle, Loader2, ChevronRight, ChevronDown, Search, Folder, FolderOpen, Cpu, HardDrive, Download, CheckSquare, Square, CheckCircle, Globe2 } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';

interface Stats {
  total_nodes: number;
  total_original_size_bytes: number;
  total_deduplicated_size_bytes: number;
  deduplication_ratio: number;
}

interface BackupHistory {
  id: number;
  node_id: number;
  archive_name: string;
  timestamp: string;
  original_size: number;
  deduplicated_size: number;
  status: string;
  comment: string | null;
}

interface Node {
  id: number;
  hostname: string;
  ip_address: string;
  is_backup_running?: boolean;
  backup_progress?: number;
  backup_task_id?: string | null;
}

import { formatDate } from './dateUtils';
import NodeDetailsModal from './NodeDetailsModal';

interface HistoryTabProps {
  onViewLogs?: (taskId: string, title: string) => void;
  timezone?: string;
  isKiosk?: boolean;
}

export default function HistoryTab({ onViewLogs, timezone, isKiosk = false }: HistoryTabProps) {
  const { t } = useTranslation();
  const [stats, setStats] = useState<Stats | null>(null);
  const [history, setHistory] = useState<BackupHistory[]>([]);
  const [nodes, setNodes] = useState<Node[]>([]);
  const [loading, setLoading] = useState(true);
  const [purgeTarget, setPurgeTarget] = useState<Node | null>(null);
  const [purging, setPurging] = useState<Record<number, boolean>>({});
  
  // Search & Grouping state
  const [searchQuery, setSearchQuery] = useState('');
  const [grouping, setGrouping] = useState<'flat' | 'hostname' | 'prefix' | 'subnet'>('hostname');
  const [expandedNodes, setExpandedNodes] = useState<Record<string, boolean>>({});
  const [selectedNodeId, setSelectedNodeId] = useState<number | null>(null);

  // Kiosk specific states
  const [viewMode, setViewMode] = useState<'local' | 'remote'>(isKiosk ? 'local' : 'remote');
  const [localHistory, setLocalHistory] = useState<BackupHistory[]>([]);
  const [storageInfo, setStorageInfo] = useState<any | null>(null);
  const [availablePaths, setAvailablePaths] = useState<string[]>([]);
  const [storagePathInput, setStoragePathInput] = useState('');
  const [selectedNodeForSync, setSelectedNodeForSync] = useState<number | null>(null);
  const [selectedArchives, setSelectedArchives] = useState<Record<string, boolean>>({});

  // Sync process state
  const [syncing, setSyncing] = useState(false);
  const [syncSpeed, setSyncSpeed] = useState<string | null>(null);
  const [syncEta, setSyncEta] = useState<string | null>(null);
  const [syncProgress, setSyncProgress] = useState<number>(0);
  const [syncingTaskId, setSyncingTaskId] = useState<string | null>(null);

  const fetchStorageInfo = async () => {
    if (!isKiosk) return;
    try {
      const res = await fetch('/api/kiosk/storage');
      if (res.ok) {
        const data = await res.json();
        setStorageInfo(data);
        if (data.path) {
          setStoragePathInput(data.path);
        }
        if (Array.isArray(data.available_paths)) {
          setAvailablePaths(data.available_paths);
        }
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleStoragePathChange = async (newPath: string) => {
    try {
      const res = await fetch('/api/kiosk/storage/path', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: newPath })
      });
      if (res.ok) {
        const data = await res.json();
        setStorageInfo(data);
        fetchStats();
      } else {
        const err = await res.json();
        alert(`Failed to set storage path: ${err.detail || 'Unknown error'}`);
      }
    } catch (e: any) {
      alert(`Error updating storage path: ${e.message}`);
    }
  };

  const handleCheckboxChange = (nodeId: number, archiveName: string, checked: boolean) => {
    setSelectedArchives(prev => {
      const next = { ...prev };
      const key = `${nodeId}-${archiveName}`;
      if (checked) {
        if (selectedNodeForSync !== nodeId) {
          setSelectedNodeForSync(nodeId);
          Object.keys(next).forEach(k => delete next[k]);
        }
        next[key] = true;
      } else {
        delete next[key];
        const remainingKeys = Object.keys(next).filter(k => next[k]);
        if (remainingKeys.length === 0) {
          setSelectedNodeForSync(null);
        }
      }
      return next;
    });
  };

  const handleCopyToLocal = async () => {
    if (selectedNodeForSync === null) return;
    const node = nodes.find(n => n.id === selectedNodeForSync);
    if (!node) return;
    
    const selectedKeys = Object.keys(selectedArchives).filter(k => selectedArchives[k]);
    const prefix = `${selectedNodeForSync}-`;
    const selectedNames = selectedKeys
      .filter(k => k.startsWith(prefix))
      .map(k => k.replace(prefix, ''));
      
    if (selectedNames.length === 0) return;
    
    setSyncing(true);
    setSyncProgress(0);
    setSyncSpeed(null);
    setSyncEta(null);
    
    try {
      const archiveParam = selectedNames.join(',');
      const res = await fetch(`/api/kiosk/sync/${node.hostname}?archive=${encodeURIComponent(archiveParam)}`, {
        method: 'POST'
      });
      if (res.ok) {
        const data = await res.json();
        if (data.task_id) {
          setSyncingTaskId(data.task_id);
          const poll = setInterval(async () => {
            try {
              const statusRes = await fetch(`/api/tasks/${data.task_id}`);
              if (statusRes.ok) {
                const statusData = await statusRes.json();
                if (statusData.download_speed) setSyncSpeed(statusData.download_speed);
                if (statusData.eta) setSyncEta(statusData.eta);
                if (typeof statusData.progress === 'number') setSyncProgress(statusData.progress);
                
                if (statusData.status === 'SUCCESS') {
                  clearInterval(poll);
                  setSyncing(false);
                  setSyncingTaskId(null);
                  setSelectedArchives({});
                  setSelectedNodeForSync(null);
                  fetchStats();
                  alert(t('copiesStarted') || 'Copy task completed successfully!');
                } else if (statusData.status === 'FAILED') {
                  clearInterval(poll);
                  setSyncing(false);
                  setSyncingTaskId(null);
                  alert('Sync failed. Please check the logs.');
                }
              } else {
                clearInterval(poll);
                setSyncing(false);
                setSyncingTaskId(null);
              }
            } catch (err) {
              clearInterval(poll);
              setSyncing(false);
              setSyncingTaskId(null);
            }
          }, 1000);
        }
      } else {
        const err = await res.json();
        alert(`Failed to start copy: ${err.detail || 'Unknown error'}`);
        setSyncing(false);
      }
    } catch (e: any) {
      alert(`Error during copy: ${e.message}`);
      setSyncing(false);
    }
  };

  const fetchStats = useCallback(async () => {
    try {
      const statsRes = await fetch('/api/stats');
      if (statsRes.ok) {
        const statsData = await statsRes.json();
        setStats(statsData);
      }

      const nodesRes = await fetch('/api/nodes');
      if (nodesRes.ok) {
        const nodesData = await nodesRes.json();
        setNodes(Array.isArray(nodesData) ? nodesData : []);
      } else {
        setNodes([]);
      }

      if (isKiosk && viewMode === 'local') {
        const histRes = await fetch('/api/kiosk/local-history');
        if (histRes.ok) {
          const histData = await histRes.json();
          setHistory(Array.isArray(histData) ? histData : []);
        } else {
          setHistory([]);
        }
      } else {
        const histRes = await fetch('/api/nodes/history');
        if (histRes.ok) {
          const histData = await histRes.json();
          setHistory(Array.isArray(histData) ? histData : []);
        } else {
          setHistory([]);
        }

        if (isKiosk) {
          const localHistRes = await fetch('/api/kiosk/local-history');
          if (localHistRes.ok) {
            const localHistData = await localHistRes.json();
            setLocalHistory(Array.isArray(localHistData) ? localHistData : []);
          } else {
            setLocalHistory([]);
          }
        }
      }
    } catch (e) {
      console.error(e);
      setNodes([]);
      setHistory([]);
    } finally {
      setLoading(false);
    }
  }, [isKiosk, viewMode]);

  useEffect(() => {
    fetchStats();
    if (isKiosk) {
      fetchStorageInfo();
    }
  }, [fetchStats, isKiosk, viewMode]);

  const toggleExpand = (key: string) => {
    setExpandedNodes(prev => ({ ...prev, [key]: !prev[key] }));
  };

  const handlePurge = async (node: Node) => {
    setPurgeTarget(null);
    setPurging(prev => ({ ...prev, [node.id]: true }));
    try {
      const res = await fetch(`/api/nodes/${node.id}/archives`, { method: 'DELETE' });
      if (res.ok) {
        const data = await res.json();
        if (data.task_id) {
          if (onViewLogs) {
            onViewLogs(data.task_id, `Purge Archives: ${node.hostname}`);
          }
          const pollInterval = setInterval(async () => {
            try {
              const taskRes = await fetch(`/api/tasks/${data.task_id}`);
              if (!taskRes.ok) {
                clearInterval(pollInterval);
                setPurging(prev => ({ ...prev, [node.id]: false }));
                return;
              }
              const taskData = await taskRes.json();
              if (taskData.status === 'SUCCESS' || taskData.status === 'FAILED') {
                clearInterval(pollInterval);
                setPurging(prev => ({ ...prev, [node.id]: false }));
                fetchStats();
              }
            } catch {
              clearInterval(pollInterval);
              setPurging(prev => ({ ...prev, [node.id]: false }));
            }
          }, 2000);
        }
      } else {
        setPurging(prev => ({ ...prev, [node.id]: false }));
      }
    } catch {
      setPurging(prev => ({ ...prev, [node.id]: false }));
    }
  };

  const getFormatSize = (bytes: number) => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`;
  };

  const getSavedSpace = () => {
    if (!stats) return '0 B';
    const diff = stats.total_original_size_bytes - stats.total_deduplicated_size_bytes;
    return getFormatSize(Math.max(0, diff));
  };

  // Node lookup
  const nodesMap = React.useMemo(() => {
    const map: Record<number, Node> = {};
    nodes.forEach(n => { map[n.id] = n; });
    return map;
  }, [nodes]);

  // Filtering history
  const filteredHistory = React.useMemo(() => {
    const q = searchQuery.toLowerCase();
    return history.filter(h => {
      const node = nodesMap[h.node_id];
      const hostname = node ? node.hostname.toLowerCase() : '';
      return (
        hostname.includes(q) ||
        h.archive_name.toLowerCase().includes(q) ||
        h.status.toLowerCase().includes(q) ||
        (h.comment && h.comment.toLowerCase().includes(q))
      );
    });
  }, [history, searchQuery, nodesMap]);

  // Group history by node ID
  const groupedByNode = React.useMemo(() => {
    const groups: Record<number, BackupHistory[]> = {};
    filteredHistory.forEach(h => {
      if (!groups[h.node_id]) groups[h.node_id] = [];
      groups[h.node_id].push(h);
    });
    return groups;
  }, [filteredHistory]);

  const renderArchiveTable = (archives: BackupHistory[], showNodeInfo = false) => (
    <div className="border-t border-zinc-800/60 bg-zinc-950/40 overflow-x-auto">
      <table className="min-w-full divide-y divide-zinc-800 text-left text-xs text-zinc-300">
        <thead className="bg-zinc-900/50 text-zinc-500 uppercase tracking-wider font-semibold">
          <tr>
            {isKiosk && viewMode === 'remote' && <th className="px-4 py-3 w-12 text-center"></th>}
            {showNodeInfo && <th className="px-6 py-3">{t('hostnameLabel')}</th>}
            {showNodeInfo && <th className="px-6 py-3">{t('ipAddressLabel')}</th>}
            <th className="px-6 py-3">{t('snapshotColumn')}</th>
            <th className="px-6 py-3">{t('timestampColumn')}</th>
            <th className="px-6 py-3">{t('originalSizeColumn')}</th>
            <th className="px-6 py-3">{t('dedupSizeColumn')}</th>
            <th className="px-6 py-3">{t('statusColumn')}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-800/50">
          {archives.map(h => {
            const node = nodesMap[h.node_id];
            const isCached = isKiosk && localHistory.some(lh => lh.archive_name === h.archive_name && lh.node_id === h.node_id);
            const selectionKey = `${h.node_id}-${h.archive_name}`;
            const isChecked = !!selectedArchives[selectionKey];

            return (
              <tr key={h.id} className={`hover:bg-zinc-900/40 transition-colors ${isChecked ? 'bg-indigo-950/20' : ''}`}>
                {isKiosk && viewMode === 'remote' && (
                  <td className="px-4 py-3.5 text-center">
                    {isCached ? (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[9px] font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20" title={t('cachedBadge') || 'Cached'}>
                        <CheckCircle size={10} />
                        <span>{t('cachedBadge') || 'Cached'}</span>
                      </span>
                    ) : (
                      <input
                        type="checkbox"
                        checked={isChecked}
                        onChange={(e) => handleCheckboxChange(h.node_id, h.archive_name, e.target.checked)}
                        className="rounded bg-zinc-900 border-zinc-800 text-indigo-600 focus:ring-0 cursor-pointer h-3.5 w-3.5"
                      />
                    )}
                  </td>
                )}
                {showNodeInfo && (
                  <td className="px-6 py-3.5 font-semibold">
                    {node ? (
                      <span
                        onClick={() => setSelectedNodeId(node.id)}
                        className="text-indigo-600 dark:text-indigo-400 hover:underline cursor-pointer"
                      >
                        {node.hostname}
                      </span>
                    ) : (
                      <span className="text-zinc-500">Unknown</span>
                    )}
                  </td>
                )}
                {showNodeInfo && (
                  <td className="px-6 py-3.5 text-zinc-400">
                    {node ? node.ip_address : 'Unknown'}
                  </td>
                )}
                <td className="px-6 py-3 flex flex-col justify-center">
                  <span className="font-semibold text-zinc-50">{h.archive_name}</span>
                  {h.comment && <span className="text-[11px] text-zinc-500 mt-0.5 italic">Comment: {h.comment}</span>}
                </td>
                <td className="px-6 py-3.5 text-zinc-400">{formatDate(h.timestamp, timezone)}</td>
                <td className="px-6 py-3.5 text-zinc-300">{getFormatSize(h.original_size)}</td>
                <td className="px-6 py-3.5 text-zinc-300">{getFormatSize(h.deduplicated_size)}</td>
                <td className="px-6 py-3.5">
                  {h.status === 'SUCCESS' ? (
                    <span className="px-2.5 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">Success</span>
                  ) : (
                    <span className="px-2.5 py-0.5 rounded-full text-[10px] font-semibold bg-rose-500/10 text-rose-400 border border-rose-500/20">Failed</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );

  const renderNodeHeader = (node: Node, subnodesCount: number, depth = 0) => {
    const isExpanded = !!expandedNodes[`node-${node.id}`];
    const success = (groupedByNode[node.id] || []).filter(h => h.status === 'SUCCESS').length;
    const failed = (groupedByNode[node.id] || []).length - success;

    return (
      <div key={`node-${node.id}`} className="rounded-xl border border-zinc-800/80 bg-zinc-950 overflow-hidden mb-2" style={{ marginLeft: `${depth * 16}px` }}>
        <button
          onClick={() => toggleExpand(`node-${node.id}`)}
          className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-zinc-900/60 transition-colors cursor-pointer group"
        >
          <div className="flex items-center gap-3">
            <ChevronRight size={16} className={`text-zinc-500 transition-transform duration-200 ${isExpanded ? 'rotate-90' : ''}`} />
            <Cpu size={14} className="text-zinc-500" />
            <span
              onClick={(e) => {
                e.stopPropagation();
                setSelectedNodeId(node.id);
              }}
              className="text-sm font-semibold text-indigo-600 dark:text-indigo-400 hover:underline cursor-pointer"
            >
              {node.hostname}
            </span>
            <span className="text-xs text-zinc-400">({node.ip_address})</span>
            <span className="text-xs text-zinc-500">— {subnodesCount} {t('snapshotColumn').toLowerCase()}(s)</span>
            {success > 0 && <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">{success} ok</span>}
            {failed > 0 && <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-rose-500/10 text-rose-400 border border-rose-500/20">{failed} {t('failed').toLowerCase()}</span>}
          </div>
          <div onClick={(e) => { e.stopPropagation(); setPurgeTarget(node); }} className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-rose-500/20 text-rose-400 hover:bg-rose-500/10 transition-colors">
            {purging[node.id] ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
            {purging[node.id] ? t('saving') : t('purgeArchives')}
          </div>
        </button>
        {isExpanded && renderArchiveTable(groupedByNode[node.id] || [])}
      </div>
    );
  };

  const renderGroupedContent = () => {
    if (grouping === 'flat') {
      return renderArchiveTable(filteredHistory, true);
    }

    if (grouping === 'hostname') {
      return nodes
        .filter(node => (groupedByNode[node.id]?.length || 0) > 0)
        .sort((a, b) => a.hostname.localeCompare(b.hostname))
        .map(node => renderNodeHeader(node, groupedByNode[node.id].length));
    }

    if (grouping === 'prefix') {
      const groups: Record<string, Node[]> = {};
      nodes.forEach(node => {
        if (!groupedByNode[node.id]?.length) return;
        const match = node.hostname.match(/^([^0-9.-]+)/);
        const prefix = match ? match[1] : 'Other';
        if (!groups[prefix]) groups[prefix] = [];
        groups[prefix].push(node);
      });

      return Object.keys(groups).sort().map(prefix => {
        const isExpanded = !!expandedGroups[prefix];
        const groupNodes = groups[prefix];
        return (
          <div key={prefix} className="mb-4">
            <button
              onClick={() => toggleExpand(prefix)}
              className="w-full flex items-center gap-2 py-2 px-3 bg-zinc-900/60 hover:bg-zinc-800/40 rounded-lg text-sm font-semibold text-zinc-300 transition-colors mb-2 cursor-pointer"
            >
              {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              {isExpanded ? <FolderOpen size={14} className="text-indigo-400" /> : <Folder size={14} className="text-indigo-400" />}
              <span>{prefix} ({groupNodes.length} node{groupNodes.length > 1 ? 's' : ''})</span>
            </button>
            {isExpanded && groupNodes.map(node => renderNodeHeader(node, groupedByNode[node.id].length, 1))}
          </div>
        );
      });
    }

    if (grouping === 'subnet') {
      const rootTree: any = {};
      nodes.forEach(node => {
        if (!groupedByNode[node.id]?.length) return;
        const parts = node.ip_address.split('.');
        if (parts.length !== 4) return;
        const o1 = parts[0] + '.x.x.x';
        const o2 = parts[0] + '.' + parts[1] + '.x.x';
        const o3 = parts[0] + '.' + parts[1] + '.' + parts[2] + '.x';

        if (!rootTree[o1]) rootTree[o1] = {};
        if (!rootTree[o1][o2]) rootTree[o1][o2] = {};
        if (!rootTree[o1][o2][o3]) rootTree[o1][o2][o3] = [];
        rootTree[o1][o2][o3].push(node);
      });

      return Object.keys(rootTree).sort().map(o1 => {
        const isO1Expanded = !!expandedGroups[o1];
        const o2Tree = rootTree[o1];
        return (
          <div key={o1} className="mb-2">
            <button onClick={() => toggleExpand(o1)} className="w-full flex items-center gap-2 py-2 px-3 bg-zinc-900/80 hover:bg-zinc-850 rounded-lg text-sm font-semibold text-zinc-200 cursor-pointer">
              {isO1Expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              <Folder size={14} className="text-zinc-400" />
              <span>Subnet: {o1}</span>
            </button>
            {isO1Expanded && Object.keys(o2Tree).sort().map(o2 => {
              const o2Key = `${o1}/${o2}`;
              const isO2Expanded = !!expandedGroups[o2Key];
              const o3Tree = o2Tree[o2];
              return (
                <div key={o2Key} className="ml-4 mt-2">
                  <button onClick={() => toggleExpand(o2Key)} className="w-full flex items-center gap-2 py-1.5 px-3 bg-zinc-900/40 hover:bg-zinc-800/30 rounded-lg text-xs font-semibold text-zinc-300 cursor-pointer">
                    {isO2Expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                    <Folder size={12} className="text-zinc-500" />
                    <span>Subnet: {o2}</span>
                  </button>
                  {isO2Expanded && Object.keys(o3Tree).sort().map(o3 => {
                    const o3Key = `${o2Key}/${o3}`;
                    const isO3Expanded = !!expandedGroups[o3Key];
                    const subnetNodes = o3Tree[o3];
                    return (
                      <div key={o3Key} className="ml-4 mt-2">
                        <button onClick={() => toggleExpand(o3Key)} className="w-full flex items-center gap-2 py-1.5 px-3 bg-zinc-900/20 hover:bg-zinc-800/20 rounded-lg text-[11px] font-semibold text-zinc-400 cursor-pointer">
                          {isO3Expanded ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
                          <FolderOpen size={10} className="text-indigo-400/80" />
                          <span>Subnet: {o3} ({subnetNodes.length})</span>
                        </button>
                        {isO3Expanded && subnetNodes.map((node: Node) => renderNodeHeader(node, groupedByNode[node.id].length, 1))}
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
        );
      });
    }

    return null;
  };

  const expandedGroups = expandedNodes; // alias

  return (
    <div className="space-y-6">
      {/* Confirmation Modal */}
      {purgeTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-fade-in">
          <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-6 w-full max-w-md shadow-xl animate-modal-in">
            <div className="flex items-center gap-3 mb-4">
              <div className="p-2.5 bg-rose-500/10 rounded-xl border border-rose-500/20">
                <AlertTriangle className="text-rose-400" size={22} />
              </div>
              <h3 className="text-lg font-bold text-zinc-50">{t('purgeWarningTitle')}</h3>
            </div>
            <p className="text-sm text-zinc-300 mb-1">
              You are about to delete <strong className="text-zinc-50">all backup archives</strong> for:
            </p>
            <p className="text-base font-semibold text-rose-400 mb-3">{purgeTarget.hostname}</p>
            <p className="text-xs text-zinc-500 mb-6">
              The Borg repository will remain initialized. This action cannot be undone.
            </p>
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setPurgeTarget(null)}
                className="px-4 py-2 text-sm rounded-lg border border-zinc-700 text-zinc-300 hover:bg-zinc-800 transition-colors"
              >
                {t('cancel')}
              </button>
              <button
                onClick={() => handlePurge(purgeTarget)}
                className="px-4 py-2 text-sm rounded-lg bg-rose-600 hover:bg-rose-500 text-white font-semibold transition-colors"
              >
                {t('purgeArchives')}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Kiosk Mode Toggle */}
      {isKiosk && (
        <div className="flex bg-zinc-950 p-1.5 gap-1.5 border border-zinc-800 rounded-xl max-w-md mb-6 shadow-lg">
          <button
            onClick={() => setViewMode('local')}
            className={`flex-1 py-2 text-xs font-bold rounded-lg transition-all flex items-center justify-center gap-2 ${
              viewMode === 'local'
                ? 'bg-indigo-600 text-white shadow-md'
                : 'text-zinc-400 hover:text-zinc-200'
            }`}
          >
            <HardDrive size={14} />
            <span>{t('localTab') || 'Local (USB Cache)'}</span>
          </button>
          <button
            onClick={() => setViewMode('remote')}
            className={`flex-1 py-2 text-xs font-bold rounded-lg transition-all flex items-center justify-center gap-2 ${
              viewMode === 'remote'
                ? 'bg-indigo-600 text-white shadow-md'
                : 'text-zinc-400 hover:text-zinc-200'
            }`}
          >
            <Globe2 size={14} />
            <span>{t('remoteTab') || 'Remote (Server)'}</span>
          </button>
        </div>
      )}

      {/* Local Mode Storage Path Settings */}
      {isKiosk && viewMode === 'local' && storageInfo && (
        <div className="p-4 bg-zinc-900 border border-zinc-800 rounded-2xl flex flex-col md:flex-row items-stretch md:items-center justify-between gap-6 mb-6">
          <div className="flex-1 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <div className="p-2.5 bg-indigo-500/10 text-indigo-400 rounded-xl border border-indigo-500/20">
                <HardDrive size={20} />
              </div>
              <div>
                <h4 className="text-xs font-black text-zinc-200 uppercase tracking-wider">{t('localBackupStorage')}</h4>
                <div className="flex items-center gap-2 mt-1">
                  <span className={`text-[9px] px-1.5 py-0.5 rounded font-mono font-bold ${
                    storageInfo.is_mounted
                      ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                      : 'bg-amber-500/10 text-amber-400 border border-amber-500/20'
                  }`}>
                    {storageInfo.is_mounted ? t('usbMountedBadge') : t('fallbackBadge')}
                  </span>
                  <span className="text-[10px] text-zinc-500 font-mono truncate max-w-[200px]" title={storageInfo.path}>
                    {storageInfo.path}
                  </span>
                </div>
              </div>
            </div>

            <div className="flex-1 max-w-xs space-y-1">
              <div className="flex justify-between text-[10px] font-semibold text-zinc-400">
                <span>{t('usedSpace', { size: getFormatSize(storageInfo.used) })}</span>
                <span>{((storageInfo.used / storageInfo.total) * 100).toFixed(0)}%</span>
              </div>
              <div className="w-full bg-zinc-950 h-1.5 rounded-full overflow-hidden border border-zinc-850 p-[1px]">
                <div
                  className={`h-full rounded-full transition-all duration-500 ${
                    storageInfo.free / storageInfo.total < 0.1
                      ? 'bg-rose-500'
                      : storageInfo.free / storageInfo.total < 0.25
                      ? 'bg-amber-500'
                      : 'bg-indigo-500'
                  }`}
                  style={{ width: `${(storageInfo.used / storageInfo.total) * 100}%` }}
                />
              </div>
              <div className="flex justify-between text-[10px] font-medium text-zinc-500">
                <span>{t('freeSpace')}: <span className="text-emerald-400">{getFormatSize(storageInfo.free)}</span></span>
                <span>{t('totalCapacity')}: {getFormatSize(storageInfo.total)}</span>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-3 border-t md:border-t-0 md:border-l border-zinc-800 pt-4 md:pt-0 md:pl-6">
            <div className="flex flex-col">
              <span className="text-[10px] font-bold text-zinc-400 uppercase tracking-wider mb-1">Mount Path</span>
              <select
                value={storageInfo.path}
                onChange={async (e) => {
                  const val = e.target.value;
                  if (val === '__custom__') {
                    const custom = prompt("Enter custom absolute storage path:", storageInfo.path);
                    if (custom && custom.trim().startsWith("/")) {
                      await handleStoragePathChange(custom.trim());
                    }
                  } else {
                    await handleStoragePathChange(val);
                  }
                }}
                className="bg-zinc-950 text-zinc-300 border border-zinc-800 rounded-lg px-2.5 py-1.5 text-xs focus:ring-0 w-44 truncate cursor-pointer hover:border-zinc-700 transition-colors font-mono"
              >
                {(storageInfo.potential_paths || [storageInfo.path]).map((p: string) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
                <option value="__custom__">⚙️ Custom Path...</option>
              </select>
            </div>
          </div>
        </div>
      )}

      {/* Metric Cards (Reduced vertical height by 50% as requested) */}
      {(!isKiosk || viewMode === 'remote') && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div className="p-3 bg-zinc-900 border border-zinc-800 rounded-xl flex items-center gap-3">
            <div className="p-1.5 bg-indigo-500/10 text-indigo-400 rounded-lg border border-indigo-500/20">
              <Database size={16} />
            </div>
            <div>
              <p className="text-[10px] text-zinc-400 font-medium uppercase tracking-wider">{t('originalSizeColumn')}</p>
              <h4 className="text-base font-bold text-zinc-50 mt-0.5">
                {stats ? getFormatSize(stats.total_deduplicated_size_bytes) : '0 B'}
              </h4>
              <p className="text-[9px] text-zinc-500">Physical size on central storage</p>
            </div>
          </div>

          <div className="p-3 bg-zinc-900 border border-zinc-800 rounded-xl flex items-center gap-3">
            <div className="p-1.5 bg-emerald-500/10 text-emerald-400 rounded-lg border border-emerald-500/20">
              <ArrowDownCircle size={16} />
            </div>
            <div>
              <p className="text-[10px] text-zinc-400 font-medium uppercase tracking-wider">{t('originalSizeColumn')}</p>
              <h4 className="text-base font-bold text-zinc-50 mt-0.5">
                {stats ? getFormatSize(stats.total_original_size_bytes) : '0 B'}
              </h4>
              <p className="text-[9px] text-emerald-400">Total size before deduplication</p>
            </div>
          </div>

          <div className="p-3 bg-zinc-900 border border-zinc-800 rounded-xl flex items-center gap-3">
            <div className="p-1.5 bg-purple-500/10 text-purple-400 rounded-lg border border-purple-500/20">
              <TrendingDown size={16} />
            </div>
            <div>
              <p className="text-[10px] text-zinc-400 font-medium uppercase tracking-wider">{t('localBackupStorage')}</p>
              <h4 className="text-base font-bold text-zinc-50 mt-0.5">{getSavedSpace()}</h4>
              <p className="text-[9px] text-purple-400">
                {t('dedupRatio')} {stats ? stats.deduplication_ratio : '1.0'}x
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Execution History */}
      <div className="p-6 bg-zinc-900 border border-zinc-800 rounded-2xl space-y-4">
        <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
          <div>
            <h3 className="text-lg font-bold text-zinc-50">{t('tabHistory')}</h3>
            <p className="text-xs text-zinc-400">{t('historySub')}</p>
          </div>
          <button
            onClick={fetchStats}
            className="p-1.5 hover:bg-zinc-800 text-zinc-400 hover:text-zinc-50 rounded transition-colors self-end"
          >
            <RefreshCw size={16} />
          </button>
        </div>

        {/* Bulk copy action panel */}
        {isKiosk && viewMode === 'remote' && (
          <div className="animate-fade-in">
            {syncing ? (
              <div className="p-4 bg-indigo-950/20 border border-indigo-900/30 rounded-xl space-y-3">
                <div className="flex items-center justify-between text-xs font-mono text-zinc-400">
                  <span className="flex items-center gap-2">
                    <Loader2 size={13} className="text-indigo-400 animate-spin" />
                    <span>
                      {syncProgress === 0 && !syncSpeed
                        ? t('syncPreparing') || 'Preparing backup archive on orchestrator (please wait)...'
                        : `${t('syncingText') || 'Syncing...'} ${syncSpeed ? `(${syncSpeed}, ETA: ${syncEta})` : ''}`
                      }
                    </span>
                  </span>
                  <span className="font-bold">{syncProgress}%</span>
                </div>
                <div className="w-full bg-zinc-950 h-2 rounded-full overflow-hidden border border-zinc-800">
                  <div
                    className="h-full bg-indigo-500 rounded-full transition-all duration-500 animate-pulse"
                    style={{ width: `${syncProgress}%` }}
                  />
                </div>
              </div>
            ) : selectedNodeForSync !== null ? (
              <div className="p-4 bg-zinc-950/60 border border-zinc-800/80 rounded-xl flex items-center justify-between gap-4">
                <div className="flex items-center gap-3">
                  <div className="p-2 bg-indigo-600/15 text-indigo-400 rounded-lg border border-indigo-500/20">
                    <Download size={16} />
                  </div>
                  <div>
                    <p className="text-xs font-bold text-zinc-200">
                      Ready to copy archives
                    </p>
                    <p className="text-[10px] text-zinc-400 mt-0.5">
                      Selected {Object.keys(selectedArchives).filter(k => selectedArchives[k]).length} archive(s) from node:{' '}
                      <span className="font-semibold text-indigo-400">
                        {nodes.find(n => n.id === selectedNodeForSync)?.hostname || 'Unknown'}
                      </span>
                    </p>
                  </div>
                </div>
                <button
                  onClick={handleCopyToLocal}
                  className="flex items-center gap-1.5 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-bold transition-all cursor-pointer shadow-lg shadow-indigo-600/20 hover:shadow-indigo-600/35"
                >
                  <Download size={14} />
                  <span>{t('copyToLocal') || 'Copy to Local'}</span>
                </button>
              </div>
            ) : null}
          </div>
        )}

        {/* Search & Grouping Controls */}
        <div className="flex flex-col md:flex-row justify-between items-stretch md:items-center gap-4 bg-zinc-900/40 p-4 rounded-xl border border-zinc-800">
          <div className="relative flex-1">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
            <input
              type="text"
              placeholder={t('searchPlaceholder')}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-9 pr-4 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm placeholder-zinc-500 focus:border-indigo-500 focus:outline-none"
            />
          </div>
          <div className="flex items-center gap-2 border-l border-zinc-800 pl-0 md:pl-4">
            <span className="text-xs text-zinc-400 font-medium whitespace-nowrap">{t('levelLabel')}:</span>
            <div className="inline-flex rounded-lg border border-zinc-800 p-0.5 bg-zinc-950">
              {(['flat', 'hostname', 'prefix', 'subnet'] as const).map(mode => (
                <button
                  key={mode}
                  onClick={() => setGrouping(mode)}
                  className={`px-3 py-1 text-xs font-semibold rounded-md transition-colors capitalize ${grouping === mode ? 'bg-indigo-600 text-white' : 'text-zinc-400 hover:text-zinc-50'}`}
                >
                  {mode === 'flat' ? t('flatView') : mode === 'hostname' ? t('hostnameLabel') : mode === 'prefix' ? t('prefixGrouping') : t('subnetGrouping')}
                </button>
              ))}
            </div>
          </div>
        </div>

        {loading ? (
          <div className="text-center py-8 text-zinc-500 text-sm">Loading...</div>
        ) : filteredHistory.length === 0 ? (
          <div className="text-center py-8 text-zinc-500 text-sm">{t('noHistoryFound')}</div>
        ) : (
          <div className="space-y-2">
            {grouping === 'flat' ? (
              <div className="rounded-xl border border-zinc-800 bg-zinc-950 overflow-hidden">
                {renderGroupedContent()}
              </div>
            ) : (
              renderGroupedContent()
            )}
          </div>
        )}

        {selectedNodeId !== null && (
          <NodeDetailsModal
            nodeId={selectedNodeId}
            onClose={() => setSelectedNodeId(null)}
            onRefreshList={fetchStats}
          />
        )}
      </div>
    </div>
  );
}
