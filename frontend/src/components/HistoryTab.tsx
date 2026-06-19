import React, { useState, useEffect, useCallback } from 'react';
import { Database, TrendingDown, ArrowDownCircle, RefreshCw, Trash2, AlertTriangle, Loader2, ChevronRight, ChevronDown, Search, Folder, FolderOpen, Cpu } from 'lucide-react';
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
}

import { formatDate } from './dateUtils';
import NodeDetailsModal from './NodeDetailsModal';

interface HistoryTabProps {
  onViewLogs?: (taskId: string, title: string) => void;
  timezone?: string;
}

export default function HistoryTab({ onViewLogs, timezone }: HistoryTabProps) {
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

      const histRes = await fetch('/api/nodes/history');
      if (histRes.ok) {
        const histData = await histRes.json();
        setHistory(Array.isArray(histData) ? histData : []);
      } else {
        setHistory([]);
      }
    } catch (e) {
      console.error(e);
      setNodes([]);
      setHistory([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStats();
  }, [fetchStats]);

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
    <div className="border-t border-zinc-800/60 bg-zinc-950/40">
      <table className="min-w-full divide-y divide-zinc-800 text-left text-xs text-zinc-300">
        <thead className="bg-zinc-900/50 text-zinc-500 uppercase tracking-wider font-semibold">
          <tr>
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
            return (
              <tr key={h.id} className="hover:bg-zinc-900/40 transition-colors">
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

      {/* Metric Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="p-6 bg-zinc-900 border border-zinc-800 rounded-2xl flex items-center gap-4">
          <div className="p-3.5 bg-indigo-500/10 text-indigo-400 rounded-xl border border-indigo-500/20">
            <Database size={24} />
          </div>
          <div>
            <p className="text-xs text-zinc-400 font-medium uppercase tracking-wider">{t('originalSizeColumn')}</p>
            <h4 className="text-xl font-bold text-zinc-50 mt-1">
              {stats ? getFormatSize(stats.total_deduplicated_size_bytes) : '0 B'}
            </h4>
            <p className="text-[10px] text-zinc-500 mt-0.5">Physical size on central storage</p>
          </div>
        </div>

        <div className="p-6 bg-zinc-900 border border-zinc-800 rounded-2xl flex items-center gap-4">
          <div className="p-3.5 bg-emerald-500/10 text-emerald-400 rounded-xl border border-emerald-500/20">
            <ArrowDownCircle size={24} />
          </div>
          <div>
            <p className="text-xs text-zinc-400 font-medium uppercase tracking-wider">{t('originalSizeColumn')}</p>
            <h4 className="text-xl font-bold text-zinc-50 mt-1">
              {stats ? getFormatSize(stats.total_original_size_bytes) : '0 B'}
            </h4>
            <p className="text-[10px] text-emerald-400 mt-0.5">Total size before deduplication</p>
          </div>
        </div>

        <div className="p-6 bg-zinc-900 border border-zinc-800 rounded-2xl flex items-center gap-4">
          <div className="p-3.5 bg-purple-500/10 text-purple-400 rounded-xl border border-purple-500/20">
            <TrendingDown size={24} />
          </div>
          <div>
            <p className="text-xs text-zinc-400 font-medium uppercase tracking-wider">{t('localBackupStorage')}</p>
            <h4 className="text-xl font-bold text-zinc-50 mt-1">{getSavedSpace()}</h4>
            <p className="text-[10px] text-purple-400 mt-0.5">
              {t('dedupRatio')} {stats ? stats.deduplication_ratio : '1.0'}x
            </p>
          </div>
        </div>
      </div>

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
