import React from 'react';
import { Cpu, CheckCircle, AlertTriangle, Settings as Gear, ShieldAlert, Trash2 } from 'lucide-react';

export interface Node {
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

interface NodeRowProps {
  node: Node;
  depth?: number;
  bulkDeleteMode: boolean;
  selectedNodeIds: Record<number, boolean>;
  onSelectNode: (nodeId: number, checked: boolean) => void;
  onRunPrepare: (nodeId: number, hostname: string) => void;
  onShowProvision: (node: Node) => void;
  onShowBackup: (node: Node) => void;
  onDeleteNode: (nodeId: number, hostname: string) => void;
}

export function NodeRow({
  node,
  depth = 0,
  bulkDeleteMode,
  selectedNodeIds,
  onSelectNode,
  onRunPrepare,
  onShowProvision,
  onShowBackup,
  onDeleteNode,
}: NodeRowProps) {
  
  const renderStatusButton = () => {
    const statusMap: Record<string, { bg: string, text: string, border: string, label: string, icon: React.ReactNode, title: string, onClick: () => void }> = {
      READY: {
        bg: "bg-emerald-500/10 hover:bg-emerald-500/20", text: "text-emerald-400", border: "border-emerald-500/20",
        label: "Ready [OK]", icon: <CheckCircle size={14} />, title: "Re-run Prepare Disk",
        onClick: () => onRunPrepare(node.id, node.hostname)
      },
      NEEDS_FIX: {
        bg: "bg-amber-500/10 hover:bg-amber-500/20", text: "text-amber-400", border: "border-amber-500/20",
        label: "Needs Fix [Prepare]", icon: <AlertTriangle size={14} />, title: "Run Prepare Disk",
        onClick: () => onRunPrepare(node.id, node.hostname)
      },
      NEEDS_BOOTSTRAP: {
        bg: "bg-zinc-500/10 hover:bg-zinc-500/20", text: "text-zinc-400", border: "border-zinc-500/20",
        label: "Provision", icon: <Gear size={14} />, title: "Provision Node",
        onClick: () => onShowProvision(node)
      },
      OFFLINE: {
        bg: "bg-rose-500/10 hover:bg-rose-500/20", text: "text-rose-400", border: "border-rose-500/20",
        label: "Provision", icon: <ShieldAlert size={14} />, title: "Provision Offline Node",
        onClick: () => onShowProvision(node)
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

  return (
    <tr className="hover:bg-zinc-800/30 transition-colors">
      {bulkDeleteMode && (
        <td className="px-4 py-2.5 w-10 text-center">
          <input
            type="checkbox"
            checked={!!selectedNodeIds[node.id]}
            onChange={(e) => onSelectNode(node.id, e.target.checked)}
            className="rounded border-zinc-800 bg-zinc-950 text-indigo-600 focus:ring-indigo-500 h-4 w-4 cursor-pointer"
          />
        </td>
      )}
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
      <td className="px-4 py-2.5">{renderStatusButton()}</td>
      <td className="px-4 py-2.5 text-zinc-400">
        {node.last_backup ? new Date(node.last_backup).toLocaleString() : 'Never'}
      </td>
      <td className="px-4 py-2.5 text-right flex items-center justify-end gap-2 text-zinc-300">
        <button
          onClick={() => onShowBackup(node)}
          disabled={node.status !== 'READY'}
          className="px-2.5 py-1.5 text-xs font-semibold bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-400 rounded border border-indigo-500/20 disabled:opacity-30 transition-colors"
        >
          Backup
        </button>
        <button
          onClick={() => onDeleteNode(node.id, node.hostname)}
          className="p-1.5 bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 rounded border border-rose-500/20 transition-colors"
          title="Delete Node"
        >
          <Trash2 size={14} />
        </button>
      </td>
    </tr>
  );
}
