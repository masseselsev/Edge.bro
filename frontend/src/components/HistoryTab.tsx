import React, { useState, useEffect, useCallback, useRef } from 'react';
import { RefreshCw, Trash2, AlertTriangle, Loader2, ChevronRight, ChevronDown, Search, Folder, FolderOpen, Cpu, HardDrive, Download, CheckSquare, Square, CheckCircle, Globe2, FileText, Eraser } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';
import ArchiveFilesModal from './ArchiveFilesModal';
import ArchiveStatsPanel from './ArchiveStatsPanel';

import { formatDate } from './dateUtils';
import NodeDetailsModal from './NodeDetailsModal';
import { formatBytes, downloadSizeBytes, isExactDownloadSize } from './formatBytes';
import type { BackupHistory, Node } from '../types';
import { useKioskArchiveSync } from '../hooks/useKioskArchiveSync';
import KioskStoragePanel from './KioskStoragePanel';

interface HistoryTabProps {
  onViewLogs?: (taskId: string, title: string) => void;
  timezone?: string;
  isKiosk?: boolean;
}

export default function HistoryTab({ onViewLogs, timezone, isKiosk = false }: HistoryTabProps) {
  const { t } = useTranslation();
  const [history, setHistory] = useState<BackupHistory[]>([]);
  const [nodes, setNodes] = useState<Node[]>([]);
  const [loading, setLoading] = useState(true);
  const [purgeTarget, setPurgeTarget] = useState<Node | null>(null);
  const [purging, setPurging] = useState<Record<number, boolean>>({});

  // Bumped whenever something the statistics depend on changes, so the panel
  // refetches instead of showing figures that include records just deleted.
  const [statsReload, setStatsReload] = useState(0);
  const refreshStatsPanel = () => setStatsReload(v => v + 1);

  // Removing failed records: one row, or every failure on a node.
  const [deletingRecord, setDeletingRecord] = useState<Record<number, boolean>>({});
  const [purgeFailedTarget, setPurgeFailedTarget] = useState<{ node: Node; count: number } | null>(null);
  const [purgingFailed, setPurgingFailed] = useState(false);
  
  // Search & Grouping state
  const [searchQuery, setSearchQuery] = useState('');
  const [grouping, setGrouping] = useState<'flat' | 'hostname' | 'prefix' | 'subnet'>('hostname');
  const [expandedNodes, setExpandedNodes] = useState<Record<string, boolean>>({});
  const [selectedNodeId, setSelectedNodeId] = useState<number | null>(null);
  const [selectedArchiveForFiles, setSelectedArchiveForFiles] = useState<{ id: number; name: string } | null>(null);

  // Pagination & Sorting states
  const [page, setPage] = useState(1);
  const [limit, setLimit] = useState(50);
  const [totalHistory, setTotalHistory] = useState(0);
  const [totalPages, setTotalPages] = useState(1);
  const [sortKey, setSortKey] = useState<string>('timestamp');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');

  // Kiosk view state. Which source the tab reads from stays here because it
  // decides which endpoint fetchStats hits; everything to do with copying
  // archives onto local storage is in useKioskArchiveSync.
  const [viewMode, setViewMode] = useState<'local' | 'remote'>(isKiosk ? 'local' : 'remote');
  const [localHistory, setLocalHistory] = useState<BackupHistory[]>([]);
  const [hasCheckedInitialLocal, setHasCheckedInitialLocal] = useState(false);
  const [remoteLoading, setRemoteLoading] = useState(false);

  const fetchStats = useCallback(async () => {
    try {
      const nodesRes = await fetch('/api/nodes');
      if (nodesRes.ok) {
        const nodesData = await nodesRes.json();
        setNodes(Array.isArray(nodesData) ? nodesData : (nodesData.nodes || []));
      } else {
        setNodes([]);
      }

      if (isKiosk && viewMode === 'local') {
        const histRes = await fetch('/api/kiosk/local-history');
        if (histRes.ok) {
          const histData = await histRes.json();
          const parsedHist = Array.isArray(histData) ? histData : [];
          setHistory(parsedHist);
          
          if (!hasCheckedInitialLocal) {
            setHasCheckedInitialLocal(true);
            if (parsedHist.length === 0) {
              setViewMode('remote');
            }
          }
        } else {
          setHistory([]);
          if (!hasCheckedInitialLocal) {
            setHasCheckedInitialLocal(true);
            setViewMode('remote');
          }
        }
      } else {
        if (isKiosk && viewMode === 'remote') {
          setRemoteLoading(true);
        }
        
        const qParams = new URLSearchParams({
          page: String(page),
          limit: String(limit),
          sort_by: sortKey,
          sort_order: sortOrder
        });
        if (searchQuery) {
          qParams.append('q', searchQuery);
        }
        
        const histRes = await fetch(`/api/nodes/history?${qParams.toString()}`);
        if (histRes.ok) {
          const histData = await histRes.json();
          setHistory(histData.history || []);
          setTotalHistory(histData.total || 0);
          setTotalPages(histData.pages || 1);
        } else {
          setHistory([]);
          setTotalHistory(0);
          setTotalPages(1);
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
      setRemoteLoading(false);
    }
  }, [isKiosk, viewMode, hasCheckedInitialLocal, page, limit, sortKey, sortOrder, searchQuery]);

  const sync = useKioskArchiveSync({ isKiosk, nodes, onStorageChanged: fetchStats });

  useEffect(() => {
    fetchStats();
    if (isKiosk) {
      sync.refreshStorageInfo();
    }
    // refreshStorageInfo is stable; listing the whole hook result would re-run
    // this on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

  /** Drop one failed record. Successful archives are refused by the API. */
  const handleDeleteRecord = async (record: BackupHistory) => {
    if (!window.confirm(t('deleteFailedConfirmText', { name: record.archive_name }))) return;

    setDeletingRecord(prev => ({ ...prev, [record.id]: true }));
    try {
      const res = await fetch(`/api/nodes/history/${record.id}`, { method: 'DELETE' });
      if (res.ok) {
        setHistory(prev => prev.filter(h => h.id !== record.id));
        refreshStatsPanel();
        fetchStats();
      } else {
        const err = await res.json().catch(() => ({}));
        alert(err.detail || 'Failed to delete the record.');
      }
    } catch (e: any) {
      alert(e.message);
    } finally {
      setDeletingRecord(prev => ({ ...prev, [record.id]: false }));
    }
  };

  /** Clear every failed record for one node — the "controlled test runs" case. */
  const handlePurgeFailed = async (node: Node) => {
    setPurgingFailed(true);
    try {
      const res = await fetch('/api/nodes/history/purge-failed', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ node_id: node.id })
      });
      if (res.ok) {
        const data = await res.json();
        setPurgeFailedTarget(null);
        refreshStatsPanel();
        await fetchStats();
        if (data.deleted === 0) alert(t('purgeFailedNothing'));
      } else {
        const err = await res.json().catch(() => ({}));
        alert(err.detail || 'Failed to purge records.');
      }
    } catch (e: any) {
      alert(e.message);
    } finally {
      setPurgingFailed(false);
    }
  };

  // Node lookup
  const nodesMap = React.useMemo(() => {
    const map: Record<number, Node> = {};
    nodes.forEach(n => { map[n.id] = n; });
    return map;
  }, [nodes]);

  const handleSort = (key: string) => {
    if (sortKey === key) {
      setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(key);
      setSortOrder('asc');
    }
    setPage(1);
  };

  const renderSortIndicator = (key: string) => {
    if (sortKey !== key) return null;
    return sortOrder === 'asc' ? (
      <span className="ml-1 text-indigo-400 font-bold">▲</span>
    ) : (
      <span className="ml-1 text-indigo-400 font-bold">▼</span>
    );
  };

  // Keep local mode pagination & totals updated
  useEffect(() => {
    if (isKiosk && viewMode === 'local') {
      const q = searchQuery.toLowerCase();
      const filtered = history.filter(h => {
        const node = nodesMap[h.node_id];
        const hostname = node ? node.hostname.toLowerCase() : '';
        return (
          hostname.includes(q) ||
          h.archive_name.toLowerCase().includes(q) ||
          h.status.toLowerCase().includes(q) ||
          (h.comment && h.comment.toLowerCase().includes(q))
        );
      });
      setTotalHistory(filtered.length);
      setTotalPages(Math.max(1, Math.ceil(filtered.length / limit)));
    }
  }, [history, searchQuery, nodesMap, isKiosk, viewMode, limit]);

  // Filtering, sorting and paging history
  const filteredHistory = React.useMemo(() => {
    if (isKiosk && viewMode === 'local') {
      const q = searchQuery.toLowerCase();
      const list = history.filter(h => {
        const node = nodesMap[h.node_id];
        const hostname = node ? node.hostname.toLowerCase() : '';
        return (
          hostname.includes(q) ||
          h.archive_name.toLowerCase().includes(q) ||
          h.status.toLowerCase().includes(q) ||
          (h.comment && h.comment.toLowerCase().includes(q))
        );
      });

      // Client-side sort
      list.sort((a, b) => {
        let valA: any = a[sortKey as keyof BackupHistory];
        let valB: any = b[sortKey as keyof BackupHistory];
        if (sortKey === 'hostname') {
          valA = nodesMap[a.node_id]?.hostname || '';
          valB = nodesMap[b.node_id]?.hostname || '';
        }
        
        if (valA === undefined || valA === null) return 1;
        if (valB === undefined || valB === null) return -1;

        if (typeof valA === 'string') {
          return sortOrder === 'asc' ? valA.localeCompare(valB) : valB.localeCompare(valA);
        } else {
          return sortOrder === 'asc' ? valA - valB : valB - valA;
        }
      });

      // Client-side pagination
      const offset = (page - 1) * limit;
      return list.slice(offset, offset + limit);
    }

    // In remote mode, the history is already filtered, sorted and paginated on the server
    return history;
  }, [history, searchQuery, nodesMap, isKiosk, viewMode, sortKey, sortOrder, page, limit]);

  // Group history by node ID
  const groupedByNode = React.useMemo(() => {
    const groups: Record<number, BackupHistory[]> = {};
    filteredHistory.forEach(h => {
      if (!groups[h.node_id]) groups[h.node_id] = [];
      groups[h.node_id].push(h);
    });
    return groups;
  }, [filteredHistory]);

  // Recomputed only when the selection or the visible rows change; it walks
  // and sorts the selected archives, and the panel below reads it twice.
  const selectionMetrics = React.useMemo(
    () => sync.estimateSelection(filteredHistory),
    [sync.estimateSelection, filteredHistory],
  );

  const renderArchiveTable = (archives: BackupHistory[], showNodeInfo = false) => (
    <div className="border-t border-zinc-800/60 bg-zinc-950/40 overflow-x-auto">
      <table className="min-w-full divide-y divide-zinc-800 text-left text-xs text-zinc-300">
        <thead className="bg-zinc-900/50 text-zinc-500 uppercase tracking-wider font-semibold">
          <tr>
            {isKiosk && viewMode === 'remote' && <th className="px-4 py-3 w-12 text-center"></th>}
            {showNodeInfo && (
              <th className="px-6 py-3">
                <button
                  onClick={() => handleSort('hostname')}
                  className={`flex items-center gap-1 cursor-pointer transition-colors hover:text-white ${sortKey === 'hostname' ? 'text-white font-bold' : ''}`}
                >
                  {t('hostnameLabel')}
                  {renderSortIndicator('hostname')}
                </button>
              </th>
            )}
            {showNodeInfo && <th className="px-6 py-3 text-zinc-500 font-semibold">{t('ipAddressLabel')}</th>}
            <th className="px-6 py-3">
              <button
                onClick={() => handleSort('archive_name')}
                className={`flex items-center gap-1 cursor-pointer transition-colors hover:text-white ${sortKey === 'archive_name' ? 'text-white font-bold' : ''}`}
              >
                {t('snapshotColumn')}
                {renderSortIndicator('archive_name')}
              </button>
            </th>
            <th className="px-6 py-3">
              <button
                onClick={() => handleSort('timestamp')}
                className={`flex items-center gap-1 cursor-pointer transition-colors hover:text-white ${sortKey === 'timestamp' ? 'text-white font-bold' : ''}`}
              >
                {t('timestampColumn')}
                {renderSortIndicator('timestamp')}
              </button>
            </th>
            <th className="px-6 py-3">
              <button
                onClick={() => handleSort('original_size')}
                className={`flex items-center gap-1 cursor-pointer transition-colors hover:text-white ${sortKey === 'original_size' ? 'text-white font-bold' : ''}`}
              >
                {t('originalSizeColumn')}
                {renderSortIndicator('original_size')}
              </button>
            </th>
            <th className="px-6 py-3">
              <button
                onClick={() => handleSort('deduplicated_size')}
                className={`flex items-center gap-1 cursor-pointer transition-colors hover:text-white ${sortKey === 'deduplicated_size' ? 'text-white font-bold' : ''}`}
              >
                {t('dedupSizeColumn')}
                {renderSortIndicator('deduplicated_size')}
              </button>
            </th>
            <th className="px-6 py-3 text-zinc-500 font-semibold">{t('estDownloadSizeColumn') || 'Download Size'}</th>
            <th className="px-6 py-3 text-zinc-500 font-semibold">{t('transferSpeedColumn')}</th>
            <th className="px-6 py-3">
              <button
                onClick={() => handleSort('status')}
                className={`flex items-center gap-1 cursor-pointer transition-colors hover:text-white ${sortKey === 'status' ? 'text-white font-bold' : ''}`}
              >
                {t('statusColumn')}
                {renderSortIndicator('status')}
              </button>
            </th>
            <th className="px-6 py-3 text-right text-zinc-500 font-semibold">{t('actions')}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-800/50">
          {archives.map(h => {
            const node = nodesMap[h.node_id];
            const isCached = isKiosk && localHistory.some(lh => lh.archive_name === h.archive_name && lh.node_id === h.node_id);
            const selectionKey = `${h.node_id}-${h.archive_name}`;
            const isChecked = sync.isSelected(h.node_id, h.archive_name);

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
                        onChange={(e) => sync.toggleArchive(h.node_id, h.archive_name, e.target.checked)}
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
                      <span className="text-zinc-500">{t('unknown') || 'Unknown'}</span>
                    )}
                  </td>
                )}
                {showNodeInfo && (
                  <td className="px-6 py-3.5 text-zinc-400">
                    {node ? node.ip_address : (t('unknown') || 'Unknown')}
                  </td>
                )}
                <td className="px-6 py-3 flex flex-col justify-center">
                  <span className="font-semibold text-zinc-50">{h.archive_name}</span>
                  {h.comment && <span className="text-[11px] text-zinc-500 mt-0.5 italic">{t('kioskTableComment') || 'Comment'}: {h.comment}</span>}
                </td>
                <td className="px-6 py-3.5 text-zinc-400">{formatDate(h.timestamp, timezone)}</td>
                <td className="px-6 py-3.5 text-zinc-300">{formatBytes(h.original_size)}</td>
                <td className="px-6 py-3.5 text-zinc-300">{formatBytes(h.deduplicated_size)}</td>
                <td className="px-6 py-3.5 text-zinc-300">
                  {/* Old rows have no recorded figure and fall back to an
                      estimate; say so rather than presenting a guess as
                      measured. */}
                  {isExactDownloadSize(h) ? '' : '≈ '}{formatBytes(downloadSizeBytes(h))}
                </td>
                <td className="px-6 py-3.5 text-zinc-300 whitespace-nowrap">
                  {h.avg_speed_mbps == null ? (
                    <span className="text-zinc-600">—</span>
                  ) : (
                    <span title={`${t('backupSpeedAvg')} / ${t('backupSpeedMax')}`}>
                      {h.avg_speed_mbps.toFixed(1)}
                      {h.max_speed_mbps != null && (
                        <span className="text-zinc-500"> / {h.max_speed_mbps.toFixed(1)}</span>
                      )}
                      <span className="text-zinc-600 text-[10px] ml-1">Mbit/s</span>
                    </span>
                  )}
                </td>
                <td className="px-6 py-3.5">
                  {h.status === 'SUCCESS' ? (
                    <span className="px-2.5 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">{t('statusSuccess') || 'Success'}</span>
                  ) : (
                    <span className="px-2.5 py-0.5 rounded-full text-[10px] font-semibold bg-rose-500/10 text-rose-400 border border-rose-500/20">{t('statusFailed') || 'Failed'}</span>
                  )}
                </td>
                <td className="px-6 py-3.5 text-right">
                  {h.status === 'SUCCESS' ? (
                    <button
                      onClick={() => setSelectedArchiveForFiles({ id: h.id, name: h.archive_name })}
                      className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-lg border border-indigo-500/20 text-indigo-400 hover:bg-indigo-500/10 transition-colors"
                      title={t('viewArchiveFiles')}
                    >
                      <FileText size={13} />
                      <span>{t('viewArchiveFiles')}</span>
                    </button>
                  ) : !isKiosk && (
                    <button
                      onClick={() => handleDeleteRecord(h)}
                      disabled={!!deletingRecord[h.id]}
                      className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-lg border border-rose-500/20 text-rose-400 hover:bg-rose-500/10 transition-colors disabled:opacity-40"
                      title={t('deleteFailedTooltip')}
                    >
                      {deletingRecord[h.id]
                        ? <Loader2 size={13} className="animate-spin" />
                        : <Trash2 size={13} />}
                      <span>{t('deleteFailedRecord')}</span>
                    </button>
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
          <div className="flex items-center gap-2">
            {failed > 0 && !isKiosk && (
              <div
                onClick={(e) => { e.stopPropagation(); setPurgeFailedTarget({ node, count: failed }); }}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-amber-500/20 text-amber-400 hover:bg-amber-500/10 transition-colors"
                title={t('purgeFailedTooltip')}
              >
                <Eraser size={13} />
                {t('purgeFailedForNode')}
              </div>
            )}
            <div onClick={(e) => { e.stopPropagation(); setPurgeTarget(node); }} className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-rose-500/20 text-rose-400 hover:bg-rose-500/10 transition-colors">
              {purging[node.id] ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
              {purging[node.id] ? t('saving') : t('purgeArchives')}
            </div>
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
              {t('deleteArchivesConfirmText') || 'You are about to delete all backup archives for:'}
            </p>
            <p className="text-base font-semibold text-rose-400 mb-3">{purgeTarget.hostname}</p>
            <p className="text-xs text-zinc-500 mb-6">
              {t('purgeWarningSubtext') || 'The Borg repository will remain initialized. This action cannot be undone.'}
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

      {/* Clearing failed records — separate from the archive purge above
          because it touches no restorable data. */}
      {purgeFailedTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-fade-in">
          <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-6 w-full max-w-md shadow-xl animate-modal-in">
            <div className="flex items-center gap-3 mb-4">
              <div className="p-2.5 bg-amber-500/10 rounded-xl border border-amber-500/20">
                <Eraser className="text-amber-400" size={22} />
              </div>
              <h3 className="text-lg font-bold text-zinc-50">{t('purgeFailedConfirmTitle')}</h3>
            </div>
            <p className="text-sm text-zinc-300 mb-6">
              {t('purgeFailedConfirmText', {
                count: purgeFailedTarget.count,
                target: purgeFailedTarget.node.hostname
              })}
            </p>
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setPurgeFailedTarget(null)}
                disabled={purgingFailed}
                className="px-4 py-2 text-sm rounded-lg border border-zinc-700 text-zinc-300 hover:bg-zinc-800 transition-colors disabled:opacity-40"
              >
                {t('cancel')}
              </button>
              <button
                onClick={() => handlePurgeFailed(purgeFailedTarget.node)}
                disabled={purgingFailed}
                className="px-4 py-2 text-sm rounded-lg bg-amber-600 hover:bg-amber-500 text-white font-semibold transition-colors disabled:opacity-40 inline-flex items-center gap-2"
              >
                {purgingFailed && <Loader2 size={14} className="animate-spin" />}
                {t('purgeFailedForNode')}
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
      {isKiosk && viewMode === 'local' && sync.storageInfo && (
        <KioskStoragePanel storage={sync.storageInfo} onSelectPath={sync.setStoragePath} />
      )}

      {/* Admin-only: /api/stats rejects a kiosk token, so in kiosk mode these
          cards only ever showed zeros. */}
      {!isKiosk && (
        <ArchiveStatsPanel reloadSignal={statsReload} onSelectNode={setSelectedNodeId} />
      )}

      {/* Execution History */}
      <div className="relative p-6 bg-zinc-900 border border-zinc-800 rounded-2xl space-y-4 overflow-hidden">
        {/* Loading Overlay */}
        {isKiosk && viewMode === 'remote' && remoteLoading && (
          <div className="absolute inset-0 bg-zinc-950/65 backdrop-blur-[2px] flex flex-col items-center justify-center gap-3 z-50 animate-fade-in transition-all">
            <div className="relative flex h-10 w-10">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-indigo-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-10 w-10 bg-indigo-600 flex items-center justify-center text-zinc-50 border border-indigo-400/30">
                <Loader2 className="animate-spin" size={20} />
              </span>
            </div>
            <p className="text-xs font-bold text-zinc-200 uppercase tracking-widest animate-pulse">
              {t('loadingRemoteArchives') || 'Loading Remote Archives...'}
            </p>
          </div>
        )}
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
            {sync.syncing ? (
              <div className="p-4 bg-indigo-950/20 border border-indigo-900/30 rounded-xl space-y-3">
                <div className="flex items-center justify-between text-xs font-mono text-zinc-400">
                  <span className="flex items-center gap-2">
                    <Loader2 size={13} className="text-indigo-400 animate-spin" />
                    <span>
                      {sync.progress === 0 && !sync.speed
                        ? t('syncPreparing') || 'Preparing backup archive on orchestrator (please wait)...'
                        : `${t('syncingText') || 'Syncing...'} ${sync.speed ? `(${sync.speed}, ETA: ${sync.eta})` : ''}`
                      }
                    </span>
                  </span>
                  <span className="font-bold">{sync.progress}%</span>
                </div>
                <div className="w-full bg-zinc-950 h-2 rounded-full overflow-hidden border border-zinc-800">
                  <div
                    className="h-full bg-indigo-500 rounded-full transition-all duration-500 animate-pulse"
                    style={{ width: `${sync.progress}%` }}
                  />
                </div>
              </div>
            ) : sync.selectedNodeId !== null ? (
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
                      Selected {Object.keys(sync.selectedArchives).filter(k => sync.selectedArchives[k]).length} archive(s) from node:{' '}
                      <span className="font-semibold text-indigo-400 mr-2">
                        {nodes.find(n => n.id === sync.selectedNodeId)?.hostname || 'Unknown'}
                      </span>
                      | {t('estDownloadSizeColumn') || 'Download Size'}:{' '}
                      <span className="font-bold text-emerald-400">
                        {formatBytes(selectionMetrics.totalEstimatedDownload)}
                      </span>{' '}
                      <span className="text-[9px] text-zinc-500 font-normal">
                        ({t('originalSizeColumn') || 'Original Size'}: {formatBytes(selectionMetrics.totalOriginal)})
                      </span>
                    </p>
                  </div>
                </div>
                <button
                  onClick={sync.copyToLocal}
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
          <div className="space-y-4">
            <div className="space-y-2">
              {grouping === 'flat' ? (
                <div className="rounded-xl border border-zinc-800 bg-zinc-950 overflow-hidden">
                  {renderGroupedContent()}
                </div>
              ) : (
                renderGroupedContent()
              )}
            </div>

            {/* Pagination Controls */}
            <div className="flex flex-col sm:flex-row justify-between items-center gap-4 px-6 py-4 border border-zinc-800 bg-zinc-950/20 rounded-xl text-xs font-semibold text-zinc-400">
              <div>
                {t('showingLabel') || 'Showing'}{' '}
                <span className="text-zinc-200">{totalHistory === 0 ? 0 : (page - 1) * limit + 1}</span>{' '}
                {t('toLabel') || 'to'}{' '}
                <span className="text-zinc-200">{Math.min(page * limit, totalHistory)}</span>{' '}
                {t('ofLabel') || 'of'}{' '}
                <span className="text-zinc-200">{totalHistory}</span>{' '}
                {t('archivesLabel') || 'archives'}
              </div>
              
              <div className="flex items-center gap-4">
                <div className="flex items-center gap-2">
                  <span>{t('rowsPerPage') || 'Rows per page'}:</span>
                  <select
                    value={limit}
                    onChange={(e) => {
                      setLimit(Number(e.target.value));
                      setPage(1);
                    }}
                    className="bg-zinc-950 border border-zinc-800 rounded px-1.5 py-1 text-zinc-200 text-xs focus:outline-none focus:border-indigo-500 cursor-pointer"
                  >
                    {[10, 25, 50, 100].map(val => (
                      <option key={val} value={val}>{val}</option>
                    ))}
                  </select>
                </div>

                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => setPage(prev => Math.max(1, prev - 1))}
                    disabled={page === 1}
                    className="px-2.5 py-1.5 bg-zinc-900 border border-zinc-800 rounded hover:border-zinc-700 text-zinc-300 disabled:opacity-40 disabled:cursor-not-allowed transition-all cursor-pointer"
                  >
                    {t('prev') || 'Previous'}
                  </button>
                  <span className="px-3 text-zinc-300">
                    {t('pageLabel') || 'Page'} {page} {t('ofLabel') || 'of'} {totalPages}
                  </span>
                  <button
                    type="button"
                    onClick={() => setPage(prev => Math.min(totalPages, prev + 1))}
                    disabled={page === totalPages}
                    className="px-2.5 py-1.5 bg-zinc-900 border border-zinc-800 rounded hover:border-zinc-700 text-zinc-300 disabled:opacity-40 disabled:cursor-not-allowed transition-all cursor-pointer"
                  >
                    {t('next') || 'Next'}
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        {selectedNodeId !== null && (
          <NodeDetailsModal
            nodeId={selectedNodeId}
            onClose={() => setSelectedNodeId(null)}
            onRefreshList={fetchStats}
          />
        )}

        {selectedArchiveForFiles !== null && (
          <ArchiveFilesModal
            historyId={selectedArchiveForFiles.id}
            archiveName={selectedArchiveForFiles.name}
            onClose={() => setSelectedArchiveForFiles(null)}
          />
        )}
      </div>
    </div>
  );
}
