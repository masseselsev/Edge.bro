import React, { useState, useEffect } from 'react';
import { Plus, Settings as Gear, ShieldAlert, CheckCircle, RefreshCw, AlertTriangle, Trash2, Search, Folder, FolderOpen, ChevronRight, ChevronDown, Cpu, Square, CheckSquare } from 'lucide-react';
import { AddNodeModal, ProvisionNodeModal, BackupCommentModal } from './NodeModals';
import { NodeRow } from './NodeRow';
import NodeDetailsModal from './NodeDetailsModal';
import { useTranslation } from '../context/TranslationContext';

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
  os_version: string | null;
  next_retry_at: string | null;
  group_id: number | null;
  backup_paused: boolean;
  backup_today: boolean;
  missed_window: boolean;
}

interface BackupGroup {
  id: number;
  name: string;
}

interface FleetTabProps {
  onViewLogs: (taskId: string, title: string) => void;
  timezone?: string;
}

export default function FleetTab({ onViewLogs, timezone }: FleetTabProps) {
  const { t } = useTranslation();
  const [nodes, setNodes] = useState<Node[]>([]);
  const [groups, setGroups] = useState<BackupGroup[]>([]);
  const [loading, setLoading] = useState(true);
  
  // Modals state
  const [showAddModal, setShowAddModal] = useState(false);
  const [showProvisionModal, setShowProvisionModal] = useState<Node | null>(null);
  const [showBackupModal, setShowBackupModal] = useState<Node | null>(null);
  const [selectedNodeDetails, setSelectedNodeDetails] = useState<number | null>(null);

  // Search & Grouping State
  const [searchQuery, setSearchQuery] = useState('');
  const [grouping, setGrouping] = useState<'flat' | 'prefix' | 'subnet'>('flat');
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});

  // Submitting States
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [provSubmitting, setProvSubmitting] = useState(false);
  const [provError, setProvError] = useState('');

  // Bulk Delete State
  const [bulkDeleteMode, setBulkDeleteMode] = useState(false);
  const [selectedNodeIds, setSelectedNodeIds] = useState<Record<number, boolean>>({});

  const fetchNodes = async () => {
    try {
      const [nRes, gRes] = await Promise.all([
        fetch('/api/nodes'),
        fetch('/api/groups')
      ]);
      if (nRes.ok) {
        const data = await nRes.json();
        setNodes(data);
      }
      if (gRes.ok) {
        const gData = await gRes.json();
        setGroups(gData);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchNodes();
    const interval = setInterval(fetchNodes, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleResponse = async (res: Response) => {
    const contentType = res.headers.get("content-type");
    if (contentType && contentType.includes("application/json")) {
      return res.json();
    }
    const text = await res.text();
    throw new Error(text || `Server returned status ${res.status}`);
  };

  const handleAddNode = async (payload: any) => {
    setSubmitting(true);
    setError('');
    try {
      const res = await fetch('/api/nodes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await handleResponse(res);
      if (!res.ok) throw new Error(data.detail || 'Failed to add node');

      setShowAddModal(false);
      fetchNodes();
      
      if (data.task_id) {
        onViewLogs(data.task_id, `Bootstrapping nodes`);
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleProvisionNode = async (payload: any) => {
    if (!showProvisionModal) return;
    setProvSubmitting(true);
    setProvError('');
    try {
      const res = await fetch(`/api/nodes/${showProvisionModal.id}/provision`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await handleResponse(res);
      if (!res.ok) throw new Error(data.detail || 'Failed to trigger provision');

      setShowProvisionModal(null);
      fetchNodes();
      
      if (data.task_id) {
        onViewLogs(data.task_id, `Provisioning ${showProvisionModal.hostname}`);
      }
    } catch (e: any) {
      setProvError(e.message);
    } finally {
      setProvSubmitting(false);
    }
  };

  const runPrepare = async (nodeId: number, name: string) => {
    try {
      const res = await fetch(`/api/nodes/${nodeId}/prepare`, { method: 'POST' });
      const data = await handleResponse(res);
      if (!res.ok) {
        throw new Error(data.detail || 'Failed to trigger prepare disk task.');
      }
      if (data.task_id) {
        onViewLogs(data.task_id, `Preparing Node ${name}`);
      } else {
        throw new Error('Server did not return a task ID.');
      }
    } catch (e: any) {
      console.error(e);
      alert(`Error: ${e.message}`);
    }
  };

  const runBackup = async (comment: string) => {
    if (!showBackupModal) return;
    const node = showBackupModal;
    setShowBackupModal(null);
    try {
      const res = await fetch(`/api/nodes/${node.id}/backup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ comment })
      });
      const data = await handleResponse(res);
      if (!res.ok) {
        throw new Error(data.detail || 'Failed to trigger backup task.');
      }
      if (data.task_id) {
        onViewLogs(data.task_id, `Backing up Node ${node.hostname}`);
      } else {
        throw new Error('Server did not return a task ID.');
      }
    } catch (e: any) {
      console.error(e);
      alert(`Error: ${e.message}`);
    }
  };

  const handleBulkDelete = async () => {
    const idsToDelete = Object.keys(selectedNodeIds)
      .map(Number)
      .filter(id => selectedNodeIds[id]);
    
    if (idsToDelete.length === 0) return;

    if (!window.confirm(t('bulkDeleteConfirm', { count: idsToDelete.length }))) {
      return;
    }

    setLoading(true);
    try {
      await Promise.all(idsToDelete.map(async (id) => {
        const res = await fetch(`/api/nodes/${id}`, { method: 'DELETE' });
        if (!res.ok) {
          const data = await res.json();
          throw new Error(data.detail || `Failed to delete node ${id}`);
        }
      }));
      
      setSelectedNodeIds({});
      setBulkDeleteMode(false);
      fetchNodes();
    } catch (e: any) {
      alert(`Error during bulk deletion: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleDeleteNode = async (nodeId: number, name: string) => {
    if (!window.confirm(t('deleteNodeConfirm'))) {
      return;
    }
    try {
      const res = await fetch(`/api/nodes/${nodeId}`, { method: 'DELETE' });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || 'Failed to delete node');
      }
      fetchNodes();
    } catch (e: any) {
      alert(e.message);
    }
  };

  const handleSelectNode = (nodeId: number, checked: boolean) => {
    setSelectedNodeIds(prev => ({ ...prev, [nodeId]: checked }));
  };

  const toggleGroup = (groupKey: string) => {
    setExpandedGroups(prev => ({ ...prev, [groupKey]: !prev[groupKey] }));
  };

  const filteredNodes = nodes.filter(node => {
    const q = searchQuery.toLowerCase();
    return (
      node.hostname.toLowerCase().includes(q) ||
      node.ip_address.toLowerCase().includes(q) ||
      (node.disk_type && node.disk_type.toLowerCase().includes(q)) ||
      (node.os_version && node.os_version.toLowerCase().includes(q))
    );
  });

  const renderNodeRow = (node: Node, depth = 0) => {
    const group = groups.find(g => g.id === node.group_id);
    const groupName = group ? group.name : null;
    return (
      <NodeRow
        key={node.id}
        node={node}
        depth={depth}
        bulkDeleteMode={bulkDeleteMode}
        selectedNodeIds={selectedNodeIds}
        onSelectNode={handleSelectNode}
        onRunPrepare={runPrepare}
        onShowProvision={setShowProvisionModal}
        onShowBackup={setShowBackupModal}
        onDeleteNode={handleDeleteNode}
        onShowDetails={() => setSelectedNodeDetails(node.id)}
        groupName={groupName}
        timezone={timezone}
      />
    );
  };

  const renderGroupedContent = () => {
    if (grouping === 'flat') {
      return filteredNodes.map(node => renderNodeRow(node));
    }

    if (grouping === 'prefix') {
      const groups: Record<string, Node[]> = {};
      filteredNodes.forEach(node => {
        const match = node.hostname.match(/^([^0-9.-]+)/);
        const prefix = match ? match[1] : 'Other';
        if (!groups[prefix]) groups[prefix] = [];
        groups[prefix].push(node);
      });

      return Object.keys(groups).sort().map(prefix => {
        const isExpanded = !!expandedGroups[prefix];
        const groupNodes = groups[prefix];
        return (
          <React.Fragment key={prefix}>
            <tr className="bg-zinc-900/60 cursor-pointer hover:bg-zinc-800/20 transition-colors border-y border-zinc-800" onClick={() => toggleGroup(prefix)}>
              <td colSpan={bulkDeleteMode ? 8 : 7} className="px-6 py-3 font-semibold text-zinc-200 select-none">
                <div className="flex items-center gap-2">
                  {isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                  {isExpanded ? <FolderOpen size={16} className="text-indigo-400" /> : <Folder size={16} className="text-indigo-400" />}
                  <span>{prefix} ({groupNodes.length})</span>
                </div>
              </td>
            </tr>
            {isExpanded && groupNodes.map(node => renderNodeRow(node, 1))}
          </React.Fragment>
        );
      });
    }

    if (grouping === 'subnet') {
      const rootTree: any = {};
      filteredNodes.forEach(node => {
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

      const rows: React.ReactNode[] = [];
      Object.keys(rootTree).sort().forEach(o1 => {
        const isO1Expanded = !!expandedGroups[o1];
        rows.push(
          <tr key={o1} className="bg-zinc-900/80 cursor-pointer hover:bg-zinc-800/20 transition-colors border-y border-zinc-800" onClick={() => toggleGroup(o1)}>
            <td colSpan={bulkDeleteMode ? 8 : 7} className="px-6 py-2.5 font-bold text-zinc-100 select-none">
              <div className="flex items-center gap-2">
                {isO1Expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                <Folder size={14} className="text-zinc-400" />
                <span>Subnet: {o1}</span>
              </div>
            </td>
          </tr>
        );

        if (isO1Expanded) {
          const o2Tree = rootTree[o1];
          Object.keys(o2Tree).sort().forEach(o2 => {
            const o2Key = `${o1}/${o2}`;
            const isO2Expanded = !!expandedGroups[o2Key];
            rows.push(
              <tr key={o2Key} className="bg-zinc-900/40 cursor-pointer hover:bg-zinc-800/10 transition-colors border-y border-zinc-800/50" onClick={() => toggleGroup(o2Key)}>
                <td colSpan={bulkDeleteMode ? 8 : 7} className="px-6 py-2.5 font-semibold text-zinc-300 select-none" style={{ paddingLeft: '36px' }}>
                  <div className="flex items-center gap-2">
                    {isO2Expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    <Folder size={14} className="text-zinc-500" />
                    <span>Subnet: {o2}</span>
                  </div>
                </td>
              </tr>
            );

            if (isO2Expanded) {
              const o3Tree = o2Tree[o2];
              Object.keys(o3Tree).sort().forEach(o3 => {
                const o3Key = `${o2Key}/${o3}`;
                const isO3Expanded = !!expandedGroups[o3Key];
                const subnetNodes = o3Tree[o3];
                rows.push(
                  <tr key={o3Key} className="bg-zinc-900/20 cursor-pointer hover:bg-zinc-800/5 transition-colors border-y border-zinc-800/20" onClick={() => toggleGroup(o3Key)}>
                    <td colSpan={bulkDeleteMode ? 8 : 7} className="px-6 py-2 font-medium text-zinc-400 select-none" style={{ paddingLeft: '54px' }}>
                      <div className="flex items-center gap-2">
                        {isO3Expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                        <FolderOpen size={14} className="text-indigo-400/80" />
                        <span>Subnet: {o3} ({subnetNodes.length})</span>
                      </div>
                    </td>
                  </tr>
                );

                if (isO3Expanded) {
                  subnetNodes.forEach((node: Node) => {
                    rows.push(renderNodeRow(node, 3));
                  });
                }
              });
            }
          });
        }
      });

      return rows;
    }

    return null;
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-zinc-50">{t('nodeListTitle')}</h2>
          <p className="text-sm text-zinc-400">{t('nodeListSub')}</p>
        </div>
        <div className="flex items-center gap-2 self-stretch sm:self-auto justify-end">
          <button
            onClick={() => {
              setBulkDeleteMode(!bulkDeleteMode);
              setSelectedNodeIds({});
            }}
            className={`flex items-center gap-1.5 px-3 py-2 rounded-lg font-semibold border transition-colors self-stretch sm:self-auto justify-center text-xs ${
              bulkDeleteMode
                ? 'bg-rose-600/20 border-rose-500/40 text-rose-400 hover:bg-rose-600/30'
                : 'bg-zinc-900 border-zinc-800 text-zinc-400 hover:text-white'
            }`}
            title={t('bulkDelete')}
          >
            {bulkDeleteMode ? <CheckSquare size={16} /> : <Square size={16} />}
            <Trash2 size={16} />
          </button>
          
          {bulkDeleteMode && Object.values(selectedNodeIds).filter(Boolean).length > 0 && (
            <button
              onClick={handleBulkDelete}
              className="flex items-center gap-1.5 px-3 py-2 bg-rose-600 hover:bg-rose-500 text-white rounded-lg font-semibold text-xs transition-colors self-stretch sm:self-auto justify-center"
            >
              <Trash2 size={16} /> {t('deleteSelected')} ({Object.values(selectedNodeIds).filter(Boolean).length})
            </button>
          )}

          <button
            onClick={() => setShowAddModal(true)}
            className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg font-semibold text-xs transition-colors self-stretch sm:self-auto justify-center"
          >
            <Plus size={16} /> {t('addNode')}
          </button>
        </div>
      </div>

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
            {(['flat', 'prefix', 'subnet'] as const).map(mode => (
              <button
                key={mode}
                onClick={() => setGrouping(mode)}
                className={`px-3 py-1 text-xs font-semibold rounded-md transition-colors capitalize ${grouping === mode ? 'bg-indigo-600 text-white' : 'text-zinc-400 hover:text-white'}`}
              >
                {mode === 'flat' ? t('flatView') : mode === 'prefix' ? t('prefixGrouping') : t('subnetGrouping')}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-900/50 backdrop-blur-md">
        <table className="min-w-full divide-y divide-zinc-800 text-left text-sm text-zinc-300">
          <thead className="bg-zinc-900 text-xs uppercase tracking-wider text-zinc-400">
            <tr>
              {bulkDeleteMode && (
                <th className="px-4 py-2.5 w-10 text-center">
                  <input
                    type="checkbox"
                    checked={filteredNodes.length > 0 && filteredNodes.every(n => selectedNodeIds[n.id])}
                    onChange={(e) => {
                      const checked = e.target.checked;
                      const newSelection: Record<number, boolean> = {};
                      if (checked) {
                        filteredNodes.forEach(n => { newSelection[n.id] = true; });
                      }
                      setSelectedNodeIds(newSelection);
                    }}
                    className="rounded border-zinc-800 bg-zinc-950 text-indigo-600 focus:ring-indigo-500 h-4 w-4 cursor-pointer"
                  />
                </th>
              )}
              <th className="px-4 py-2.5">{t('hostnameLabel')}</th>
              <th className="px-4 py-2.5">{t('ipAddressLabel')}</th>
              <th className="px-4 py-2.5">OS Version</th>
              <th className="px-4 py-2.5">Disk & Interface</th>
              <th className="px-4 py-2.5">Status / Action</th>
              <th className="px-4 py-2.5">Last Backup</th>
              <th className="px-4 py-2.5 text-right">{t('actions')}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800">
            {loading ? (
              <tr>
                <td colSpan={bulkDeleteMode ? 8 : 7} className="px-6 py-8 text-center text-zinc-500">Loading fleet data...</td>
              </tr>
            ) : filteredNodes.length === 0 ? (
              <tr>
                <td colSpan={bulkDeleteMode ? 8 : 7} className="px-6 py-8 text-center text-zinc-500">No nodes match your filter.</td>
              </tr>
            ) : (
              renderGroupedContent()
            )}
          </tbody>
        </table>
      </div>

      {showAddModal && <AddNodeModal onClose={() => setShowAddModal(false)} onSubmit={handleAddNode} submitting={submitting} error={error} />}
      {showProvisionModal && <ProvisionNodeModal node={showProvisionModal} onClose={() => setShowProvisionModal(null)} onSubmit={handleProvisionNode} submitting={provSubmitting} error={provError} />}
      {showBackupModal && <BackupCommentModal node={showBackupModal} onClose={() => setShowBackupModal(null)} onSubmit={runBackup} />}
      {selectedNodeDetails !== null && (
        <NodeDetailsModal
          nodeId={selectedNodeDetails}
          onClose={() => setSelectedNodeDetails(null)}
          onRefreshList={fetchNodes}
        />
      )}
    </div>
  );
}
