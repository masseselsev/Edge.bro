import React, { useState, useEffect } from 'react';
import { Plus, Settings as Gear, ShieldAlert, CheckCircle, RefreshCw, AlertTriangle, Trash2, Search, Folder, FolderOpen, ChevronRight, ChevronDown, Cpu } from 'lucide-react';
import { AddNodeModal, ProvisionNodeModal, BackupCommentModal } from './NodeModals';

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
}

interface FleetTabProps {
  onViewLogs: (taskId: string, title: string) => void;
}

export default function FleetTab({ onViewLogs }: FleetTabProps) {
  const [nodes, setNodes] = useState<Node[]>([]);
  const [loading, setLoading] = useState(true);
  
  // Modals state
  const [showAddModal, setShowAddModal] = useState(false);
  const [showProvisionModal, setShowProvisionModal] = useState<Node | null>(null);
  const [showBackupModal, setShowBackupModal] = useState<Node | null>(null);

  // Search & Grouping State
  const [searchQuery, setSearchQuery] = useState('');
  const [grouping, setGrouping] = useState<'flat' | 'prefix' | 'subnet'>('flat');
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});

  // Submitting States
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [provSubmitting, setProvSubmitting] = useState(false);
  const [provError, setProvError] = useState('');

  const fetchNodes = async () => {
    try {
      const res = await fetch('/api/nodes');
      const data = await res.json();
      setNodes(data);
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

  const handleAddNode = async (payload: any) => {
    setSubmitting(true);
    setError('');
    try {
      const res = await fetch('/api/nodes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      let data: any = {};
      const contentType = res.headers.get("content-type");
      if (contentType && contentType.includes("application/json")) {
        data = await res.json();
      } else {
        const text = await res.text();
        throw new Error(text || `Server returned status ${res.status}`);
      }
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
      let data: any = {};
      const contentType = res.headers.get("content-type");
      if (contentType && contentType.includes("application/json")) {
        data = await res.json();
      } else {
        const text = await res.text();
        throw new Error(text || `Server returned status ${res.status}`);
      }
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
      let data: any = {};
      const contentType = res.headers.get("content-type");
      if (contentType && contentType.includes("application/json")) {
        data = await res.json();
      } else {
        const text = await res.text();
        throw new Error(text || `Server returned status ${res.status}`);
      }
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
      let data: any = {};
      const contentType = res.headers.get("content-type");
      if (contentType && contentType.includes("application/json")) {
        data = await res.json();
      } else {
        const text = await res.text();
        throw new Error(text || `Server returned status ${res.status}`);
      }
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

  const handleDeleteNode = async (nodeId: number, name: string) => {
    if (!window.confirm(`Are you sure you want to delete node "${name}"? This will also remove its backup history.`)) {
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

  const renderStatusButton = (node: Node) => {
    const statusMap: Record<string, { bg: string, text: string, border: string, label: string, icon: React.ReactNode, title: string, onClick: () => void }> = {
      READY: {
        bg: "bg-emerald-500/10 hover:bg-emerald-500/20", text: "text-emerald-400", border: "border-emerald-500/20",
        label: "Ready [OK]", icon: <CheckCircle size={14} />, title: "Re-run Prepare Disk",
        onClick: () => runPrepare(node.id, node.hostname)
      },
      NEEDS_FIX: {
        bg: "bg-amber-500/10 hover:bg-amber-500/20", text: "text-amber-400", border: "border-amber-500/20",
        label: "Needs Fix [Prepare]", icon: <AlertTriangle size={14} />, title: "Run Prepare Disk",
        onClick: () => runPrepare(node.id, node.hostname)
      },
      NEEDS_BOOTSTRAP: {
        bg: "bg-zinc-500/10 hover:bg-zinc-500/20", text: "text-zinc-400", border: "border-zinc-500/20",
        label: "Provision", icon: <Gear size={14} />, title: "Provision Node",
        onClick: () => setShowProvisionModal(node)
      },
      OFFLINE: {
        bg: "bg-rose-500/10 hover:bg-rose-500/20", text: "text-rose-400", border: "border-rose-500/20",
        label: "Provision", icon: <ShieldAlert size={14} />, title: "Provision Offline Node",
        onClick: () => setShowProvisionModal(node)
      }
    };
    const config = statusMap[node.status] || statusMap.OFFLINE;
    return (
      <button
        onClick={config.onClick}
        className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold border transition-colors cursor-pointer ${config.bg} ${config.text} ${config.border}`}
        title={config.title}
      >
        {config.icon} {config.label}
      </button>
    );
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

  const renderNodeRow = (node: Node, depth = 0) => (
    <tr key={node.id} className="hover:bg-zinc-800/30 transition-colors">
      <td className="px-4 py-2.5 font-semibold text-white flex items-center gap-2" style={{ paddingLeft: `${depth * 20 + 24}px` }}>
        <Cpu size={14} className="text-zinc-500" />
        {node.hostname}
      </td>
      <td className="px-4 py-2.5 text-zinc-400">{node.ip_address}:{node.ssh_port}</td>
      <td className="px-4 py-2.5 text-zinc-300 font-medium text-xs">{node.os_version || 'Unknown'}</td>
      <td className="px-4 py-2.5">
        <div className="flex flex-col">
          <span className="text-zinc-300 font-medium text-xs">Disk: {node.disk_type}</span>
          <span className="text-zinc-500 text-xs">Net: {node.network_iface || 'UNKNOWN'}</span>
        </div>
      </td>
      <td className="px-4 py-2.5">{renderStatusButton(node)}</td>
      <td className="px-4 py-2.5 text-zinc-400">
        {node.last_backup ? new Date(node.last_backup).toLocaleString() : 'Never'}
      </td>
      <td className="px-4 py-2.5 text-right flex items-center justify-end gap-2 text-zinc-300">
        <button
          onClick={() => setShowBackupModal(node)}
          disabled={node.status !== 'READY'}
          className="px-2.5 py-1.5 text-xs font-semibold bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-400 rounded border border-indigo-500/20 disabled:opacity-30 transition-colors"
        >
          Backup
        </button>
        <button
          onClick={() => handleDeleteNode(node.id, node.hostname)}
          className="p-1.5 bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 rounded border border-rose-500/20 transition-colors"
          title="Delete Node"
        >
          <Trash2 size={14} />
        </button>
      </td>
    </tr>
  );

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
              <td colSpan={7} className="px-6 py-3 font-semibold text-zinc-200 select-none">
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
            <td colSpan={7} className="px-6 py-2.5 font-bold text-zinc-100 select-none">
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
                <td colSpan={7} className="px-6 py-2.5 font-semibold text-zinc-300 select-none" style={{ paddingLeft: '36px' }}>
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
                    <td colSpan={7} className="px-6 py-2 font-medium text-zinc-400 select-none" style={{ paddingLeft: '54px' }}>
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
          <h2 className="text-2xl font-bold tracking-tight text-white">Edge Fleet</h2>
          <p className="text-sm text-zinc-400">Manage, auto-provision and view your active Debian edge nodes.</p>
        </div>
        <button
          onClick={() => setShowAddModal(true)}
          className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg font-medium transition-colors self-stretch sm:self-auto justify-center"
        >
          <Plus size={18} /> Add Node
        </button>
      </div>

      <div className="flex flex-col md:flex-row justify-between items-stretch md:items-center gap-4 bg-zinc-900/40 p-4 rounded-xl border border-zinc-800">
        <div className="relative flex-1">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
          <input
            type="text"
            placeholder="Search nodes by hostname, IP, disk type, or OS version..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-9 pr-4 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-white text-sm placeholder-zinc-500 focus:border-indigo-500 focus:outline-none"
          />
        </div>
        <div className="flex items-center gap-2 border-l border-zinc-800 pl-0 md:pl-4">
          <span className="text-xs text-zinc-400 font-medium whitespace-nowrap">Group By:</span>
          <div className="inline-flex rounded-lg border border-zinc-800 p-0.5 bg-zinc-950">
            {(['flat', 'prefix', 'subnet'] as const).map(mode => (
              <button
                key={mode}
                onClick={() => setGrouping(mode)}
                className={`px-3 py-1 text-xs font-semibold rounded-md transition-colors capitalize ${grouping === mode ? 'bg-indigo-600 text-white' : 'text-zinc-400 hover:text-white'}`}
              >
                {mode === 'flat' ? 'Flat List' : mode === 'prefix' ? 'Hostname Prefix' : 'IP Subnet'}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-900/50 backdrop-blur-md">
        <table className="min-w-full divide-y divide-zinc-800 text-left text-sm text-zinc-300">
          <thead className="bg-zinc-900 text-xs uppercase tracking-wider text-zinc-400">
            <tr>
              <th className="px-4 py-2.5">Hostname</th>
              <th className="px-4 py-2.5">IP Address</th>
              <th className="px-4 py-2.5">OS Version</th>
              <th className="px-4 py-2.5">Disk & Interface</th>
              <th className="px-4 py-2.5">Status / Action</th>
              <th className="px-4 py-2.5">Last Backup</th>
              <th className="px-4 py-2.5 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800">
            {loading ? (
              <tr>
                <td colSpan={7} className="px-6 py-8 text-center text-zinc-500">Loading fleet data...</td>
              </tr>
            ) : filteredNodes.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-6 py-8 text-center text-zinc-500">No nodes match your filter.</td>
              </tr>
            ) : (
              renderGroupedContent()
            )}
          </tbody>
        </table>
      </div>

      {showAddModal && (
        <AddNodeModal
          onClose={() => setShowAddModal(false)}
          onSubmit={handleAddNode}
          submitting={submitting}
          error={error}
        />
      )}

      {showProvisionModal && (
        <ProvisionNodeModal
          node={showProvisionModal}
          onClose={() => setShowProvisionModal(null)}
          onSubmit={handleProvisionNode}
          submitting={provSubmitting}
          error={provError}
        />
      )}

      {showBackupModal && (
        <BackupCommentModal
          node={showBackupModal}
          onClose={() => setShowBackupModal(null)}
          onSubmit={runBackup}
        />
      )}
    </div>
  );
}
