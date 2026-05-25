import React, { useState, useEffect, useCallback } from 'react';
import { Database, TrendingDown, ArrowDownCircle, RefreshCw, Trash2, AlertTriangle, Loader2, ChevronRight } from 'lucide-react';

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
}

interface Node {
  id: number;
  hostname: string;
}

export default function HistoryTab() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [history, setHistory] = useState<BackupHistory[]>([]);
  const [nodes, setNodes] = useState<Node[]>([]);
  const [loading, setLoading] = useState(true);
  const [purgeTarget, setPurgeTarget] = useState<Node | null>(null);
  const [purging, setPurging] = useState<Record<number, boolean>>({});
  const [expandedNodes, setExpandedNodes] = useState<Record<number, boolean>>({});

  const fetchStats = useCallback(async () => {
    try {
      const statsRes = await fetch('/api/stats');
      const statsData = await statsRes.json();
      setStats(statsData);

      const nodesRes = await fetch('/api/nodes');
      const nodesData = await nodesRes.json();
      setNodes(nodesData);

      const allHistory: BackupHistory[] = [];
      for (const n of nodesData) {
        const histRes = await fetch(`/api/nodes/${n.id}/history`);
        const histData = await histRes.json();
        allHistory.push(...histData);
      }
      allHistory.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
      setHistory(allHistory);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStats();
  }, [fetchStats]);

  const toggleNode = (nodeId: number) => {
    setExpandedNodes(prev => ({ ...prev, [nodeId]: !prev[nodeId] }));
  };

  const handlePurge = async (node: Node) => {
    setPurgeTarget(null);
    setPurging(prev => ({ ...prev, [node.id]: true }));
    try {
      const res = await fetch(`/api/nodes/${node.id}/archives`, { method: 'DELETE' });
      if (res.ok) {
        const data = await res.json();
        if (data.task_id) {
          const pollInterval = setInterval(async () => {
            try {
              const taskRes = await fetch(`/api/tasks/${data.task_id}`);
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

  // Group history by node
  const groupedHistory: Record<number, BackupHistory[]> = {};
  for (const h of history) {
    if (!groupedHistory[h.node_id]) groupedHistory[h.node_id] = [];
    groupedHistory[h.node_id].push(h);
  }

  const nodesWithHistory = nodes.filter(n => groupedHistory[n.id]?.length);

  const getNodeStatusCounts = (nodeId: number) => {
    const items = groupedHistory[nodeId] || [];
    const success = items.filter(h => h.status === 'SUCCESS').length;
    const failed = items.length - success;
    return { success, failed, total: items.length };
  };

  const getLatestBackup = (nodeId: number) => {
    const items = groupedHistory[nodeId] || [];
    if (items.length === 0) return null;
    return new Date(items[0].timestamp);
  };

  return (
    <div className="space-y-6">
      {/* Confirmation Modal */}
      {purgeTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="bg-zinc-900 border border-zinc-700 rounded-2xl p-6 w-full max-w-md shadow-xl">
            <div className="flex items-center gap-3 mb-4">
              <div className="p-2.5 bg-rose-500/10 rounded-xl border border-rose-500/20">
                <AlertTriangle className="text-rose-400" size={22} />
              </div>
              <h3 className="text-lg font-bold text-white">Confirm Purge</h3>
            </div>
            <p className="text-sm text-zinc-300 mb-1">
              You are about to delete <strong className="text-white">all backup archives</strong> for:
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
                Cancel
              </button>
              <button
                onClick={() => handlePurge(purgeTarget)}
                className="px-4 py-2 text-sm rounded-lg bg-rose-600 hover:bg-rose-500 text-white font-semibold transition-colors"
              >
                Purge All Archives
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
            <p className="text-xs text-zinc-400 font-medium uppercase tracking-wider">Total Repository Data</p>
            <h4 className="text-xl font-bold text-white mt-1">
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
            <p className="text-xs text-zinc-400 font-medium uppercase tracking-wider">Original System Size</p>
            <h4 className="text-xl font-bold text-white mt-1">
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
            <p className="text-xs text-zinc-400 font-medium uppercase tracking-wider">Storage Savings</p>
            <h4 className="text-xl font-bold text-white mt-1">{getSavedSpace()}</h4>
            <p className="text-[10px] text-purple-400 mt-0.5">
              Dedup Ratio: {stats ? stats.deduplication_ratio : '1.0'}x
            </p>
          </div>
        </div>
      </div>

      {/* Collapsible history per node */}
      <div className="p-6 bg-zinc-900 border border-zinc-800 rounded-2xl space-y-3">
        <div className="flex justify-between items-center mb-2">
          <div>
            <h3 className="text-lg font-bold text-white">Execution History</h3>
            <p className="text-xs text-zinc-400">Click on a device to expand its backup archives.</p>
          </div>
          <button
            onClick={fetchStats}
            className="p-1.5 hover:bg-zinc-800 text-zinc-400 hover:text-white rounded transition-colors"
          >
            <RefreshCw size={16} />
          </button>
        </div>

        {loading ? (
          <div className="text-center py-8 text-zinc-500 text-sm">Loading history records...</div>
        ) : history.length === 0 ? (
          <div className="text-center py-8 text-zinc-500 text-sm">No backup records found.</div>
        ) : (
          <div className="space-y-2">
            {nodesWithHistory.map(node => {
              const isExpanded = !!expandedNodes[node.id];
              const counts = getNodeStatusCounts(node.id);
              const latestDate = getLatestBackup(node.id);

              return (
                <div
                  key={node.id}
                  className="rounded-xl border border-zinc-800/80 bg-zinc-950 overflow-hidden"
                >
                  {/* Clickable device header */}
                  <button
                    onClick={() => toggleNode(node.id)}
                    className="w-full flex items-center justify-between px-5 py-4 hover:bg-zinc-900/60 transition-colors cursor-pointer group"
                  >
                    <div className="flex items-center gap-3">
                      <ChevronRight
                        size={16}
                        className={`text-zinc-500 transition-transform duration-200 ${isExpanded ? 'rotate-90' : ''}`}
                      />
                      <Database size={15} className="text-zinc-500" />
                      <span className="text-sm font-semibold text-zinc-100 group-hover:text-white transition-colors">
                        {node.hostname}
                      </span>
                      <span className="text-xs text-zinc-500 font-normal">
                        — {counts.total} archive(s)
                      </span>
                      {counts.success > 0 && (
                        <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                          {counts.success} ok
                        </span>
                      )}
                      {counts.failed > 0 && (
                        <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-rose-500/10 text-rose-400 border border-rose-500/20">
                          {counts.failed} failed
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-3">
                      {latestDate && (
                        <span className="text-[11px] text-zinc-500 hidden sm:inline">
                          Last: {latestDate.toLocaleDateString()}
                        </span>
                      )}
                      <div
                        onClick={(e) => { e.stopPropagation(); setPurgeTarget(node); }}
                        className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-rose-500/20 text-rose-400 hover:bg-rose-500/10 transition-colors"
                        role="button"
                        tabIndex={0}
                      >
                        {purging[node.id] ? (
                          <Loader2 size={13} className="animate-spin" />
                        ) : (
                          <Trash2 size={13} />
                        )}
                        {purging[node.id] ? 'Purging...' : 'Purge All'}
                      </div>
                    </div>
                  </button>

                  {/* Collapsible archive table */}
                  <div
                    className="overflow-hidden transition-all duration-300 ease-in-out"
                    style={{
                      maxHeight: isExpanded ? `${(groupedHistory[node.id].length + 1) * 52 + 20}px` : '0px',
                      opacity: isExpanded ? 1 : 0,
                    }}
                  >
                    <div className="border-t border-zinc-800/60">
                      <table className="min-w-full divide-y divide-zinc-800 text-left text-xs text-zinc-300">
                        <thead className="bg-zinc-900/50 text-zinc-500 uppercase tracking-wider font-semibold">
                          <tr>
                            <th className="px-6 py-3">Archive Snapshot</th>
                            <th className="px-6 py-3">Date & Time</th>
                            <th className="px-6 py-3">Original Size</th>
                            <th className="px-6 py-3">Deduplicated Size</th>
                            <th className="px-6 py-3">Status</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-zinc-800/50">
                          {groupedHistory[node.id].map(h => (
                            <tr key={h.id} className="hover:bg-zinc-900/40 transition-colors">
                              <td className="px-6 py-3.5 font-semibold text-white">{h.archive_name}</td>
                              <td className="px-6 py-3.5 text-zinc-400">{new Date(h.timestamp).toLocaleString()}</td>
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
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
