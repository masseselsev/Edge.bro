import React from 'react';
import { Cpu, CheckCircle, AlertTriangle, Settings as Gear, ShieldAlert, Trash2 } from 'lucide-react';
import { formatDate } from './dateUtils';
import { useTranslation } from '../context/TranslationContext';

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
  next_retry_at: string | null;
  group_id: number | null;
  backup_paused: boolean;
  backup_today: boolean;
  missed_window: boolean;
  is_backup_running?: boolean;
  backup_progress?: number;
  backup_task_id?: string | null;
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
  onShowDetails: () => void;
  groupName: string | null;
  timezone?: string;
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
  onShowDetails,
  groupName,
  timezone,
}: NodeRowProps) {
  const { t } = useTranslation();
  const [timeLeft, setTimeLeft] = React.useState<number>(0);

  React.useEffect(() => {
    if (node.status !== 'OFFLINE' || !node.next_retry_at) {
      setTimeLeft(0);
      return;
    }

    const calculateTimeLeft = () => {
      const diff = new Date(node.next_retry_at!).getTime() - Date.now();
      return Math.max(0, Math.ceil(diff / 1000));
    };

    setTimeLeft(calculateTimeLeft());

    const timer = setInterval(() => {
      const remaining = calculateTimeLeft();
      setTimeLeft(remaining);
      if (remaining <= 0) {
        clearInterval(timer);
      }
    }, 1000);

    return () => clearInterval(timer);
  }, [node.status, node.next_retry_at]);

  const formatTime = (seconds: number) => {
    if (seconds <= 0) return '';
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${s < 10 ? '0' : ''}${s}`;
  };
  
  const renderStatusButton = () => {
    const statusMap: Record<string, { bg: string, text: string, border: string, label: string, icon: React.ReactNode, title: string, onClick: () => void }> = {
      READY: {
        bg: "bg-emerald-500/10 hover:bg-emerald-500/20", text: "text-emerald-400", border: "border-emerald-500/20",
        label: t('readyOk'), icon: <CheckCircle size={14} />, title: t('reRunPrepareDisk'),
        onClick: () => onRunPrepare(node.id, node.hostname)
      },
      NEEDS_FIX: {
        bg: "bg-amber-500/10 hover:bg-amber-500/20", text: "text-amber-400", border: "border-amber-500/20",
        label: t('needsFixPrepare'), icon: <AlertTriangle size={14} />, title: t('runPrepareDisk'),
        onClick: () => onRunPrepare(node.id, node.hostname)
      },
      NEEDS_BOOTSTRAP: {
        bg: "bg-zinc-500/10 hover:bg-zinc-500/20", text: "text-zinc-400", border: "border-zinc-500/20",
        label: t('statusProvision'), icon: <Gear size={14} />, title: t('provisionNodeTooltip'),
        onClick: () => onShowProvision(node)
      },
      OFFLINE: {
        bg: "bg-rose-500/10 hover:bg-rose-500/20", text: "text-rose-400", border: "border-rose-500/20",
        label: timeLeft > 0 ? t('provisionTimeLeft').replace('{time}', formatTime(timeLeft)) : t('statusProvision'),
        icon: <ShieldAlert size={14} />,
        title: timeLeft > 0 ? t('autoRetryIn').replace('{time}', formatTime(timeLeft)) : t('provisionOfflineNode'),
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
      <td className="px-4 py-2.5 font-semibold text-zinc-50 flex items-center gap-2" style={{ paddingLeft: `${depth * 20 + 24}px` }}>
        <Cpu size={14} className="text-zinc-500" />
        <div className="flex flex-col">
          <span className="break-all" title={node.hostname}>{node.hostname}</span>
          {groupName && (
            <span className="text-[10px] text-indigo-400 font-semibold leading-none mt-1">
              Group: {groupName}
            </span>
          )}
          {(node.backup_paused || node.missed_window) && (
            <div className="flex gap-1 mt-1">
              {node.backup_paused && (
                <span className="px-1.5 py-0.5 bg-amber-500/10 text-amber-400 border border-amber-500/20 rounded text-[9px] font-bold">
                  {t('paused')}
                </span>
              )}
              {node.missed_window && (
                <span className="px-1.5 py-0.5 bg-rose-500/10 text-rose-400 border border-rose-500/20 rounded text-[9px] font-bold animate-pulse">
                  {t('missedWindow')}
                </span>
              )}
            </div>
          )}
        </div>
      </td>
      <td className="px-4 py-2.5 text-zinc-400">{node.ip_address}:{node.ssh_port}</td>
      <td className="px-4 py-2.5 text-zinc-300 font-medium text-xs">{node.os_version || t('unknown')}</td>
      <td className="px-4 py-2.5">
        <div className="flex flex-col">
          <span className="text-zinc-300 font-medium text-xs">{t('diskLabel')}: {node.disk_type ? node.disk_type.split(' ')[0] : 'UNKNOWN'}</span>
          <span className="text-zinc-500 text-xs">{t('netLabel')}: {node.network_iface || t('unknown').toUpperCase()}</span>
        </div>
      </td>
      <td className="px-4 py-2.5">{renderStatusButton()}</td>
      <td className="px-4 py-2.5 text-zinc-400">
        {node.last_backup ? formatDate(node.last_backup, timezone) : t('never')}
      </td>
      <td className="px-4 py-2.5 text-right flex flex-wrap items-center justify-end gap-2 text-zinc-300 font-sans">
        <button
          onClick={onShowDetails}
          className="px-2.5 py-1.5 text-xs font-semibold bg-zinc-800 hover:bg-zinc-750 text-zinc-200 border border-zinc-700/80 rounded hover:text-indigo-400 transition-colors"
        >
          {t('nodeDetails')}
        </button>
        <button
          onClick={() => onShowBackup(node)}
          disabled={node.status !== 'READY' && !node.is_backup_running}
          style={node.is_backup_running ? {
            background: `linear-gradient(to right, rgba(99, 102, 241, 0.25) ${node.backup_progress}%, transparent ${node.backup_progress}%)`
          } : undefined}
          className={`px-2.5 py-1.5 text-xs font-semibold rounded border transition-colors ${
            node.is_backup_running
              ? 'animate-pulse text-indigo-300 border-indigo-500 bg-indigo-500/5 hover:bg-indigo-500/10 cursor-pointer'
              : 'bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-400 border-indigo-500/20 disabled:opacity-30'
          }`}
        >
          {node.is_backup_running
            ? `${t('backupAction')} (${node.backup_progress}%)`
            : t('backupAction')}
        </button>
        <button
          onClick={() => onDeleteNode(node.id, node.hostname)}
          className="p-1.5 bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 rounded border border-rose-500/20 transition-colors"
          title={t('deleteNodeTooltip')}
        >
          <Trash2 size={14} />
        </button>
      </td>
    </tr>
  );
}
