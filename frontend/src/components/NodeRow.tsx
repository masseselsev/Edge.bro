import React from 'react';
import { Cpu, CheckCircle, AlertTriangle, Settings as Gear, ShieldAlert, Trash2 } from 'lucide-react';
import { formatDate } from './dateUtils';
import { kibToMbit, formatMbit, formatLiveSpeed } from './rateLimit';
import { useTranslation } from '../context/TranslationContext';
import type { Node } from '../types';

interface NodeRowProps {
  node: Node;
  depth?: number;
  bulkDeleteMode: boolean;
  /** Just this row's state. Passing the whole selection map would change
   *  identity on every click and defeat the memo below. */
  isSelected: boolean;
  onSelectNode: (nodeId: number, checked: boolean) => void;
  onRunPrepare: (nodeId: number, hostname: string) => void;
  onShowProvision: (node: Node) => void;
  onInstantProvision: (node: Node) => void;
  onShowBackup: (node: Node) => void;
  onDeleteNode: (nodeId: number, hostname: string) => void;
  /** Takes the id so FleetTab can hold one stable callback for every row. */
  onShowDetails: (nodeId: number) => void;
  groupName: string | null;
  groupRateLimit?: number | null;
  timezone?: string;
}

function NodeRowComponent({
  node,
  depth = 0,
  bulkDeleteMode,
  isSelected,
  onSelectNode,
  onRunPrepare,
  onShowProvision,
  onInstantProvision,
  onShowBackup,
  onDeleteNode,
  onShowDetails,
  groupName,
  groupRateLimit,
  timezone,
}: NodeRowProps) {
  const { t } = useTranslation();
  const [timeLeft, setTimeLeft] = React.useState<number>(0);
  const liveSpeed = node.is_backup_running ? formatLiveSpeed(node) : null;

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

  const getIpColorClass = () => {
    if (node.last_ping_status === true) {
      return "text-emerald-400 font-medium";
    }
    if (node.last_available_at) {
      return "text-rose-400 font-medium";
    }
    return "text-amber-500 font-medium"; // Never online / orange
  };

  const getIpTooltip = () => {
    if (node.last_ping_status === true) {
      return t('nodeOnline') || 'Online';
    }
    if (node.last_available_at) {
      const formattedTime = formatDate(node.last_available_at, timezone);
      return (t('nodeOfflineLastSeen') || 'Offline (Last seen: {time})').replace('{time}', formattedTime);
    }
    return t('nodeNeverOnline') || 'Never online';
  };

  const effectiveLimitStr = (() => {
    if (node.upload_rate_limit != null && node.upload_rate_limit > 0) {
      return `${formatMbit(kibToMbit(node.upload_rate_limit))} Mbit/s`;
    }
    if (groupRateLimit != null && groupRateLimit > 0) {
      return `${formatMbit(kibToMbit(groupRateLimit))} Mbit/s`;
    }
    return t('rateLimitUnlimited') || 'unlimited';
  })();
  
  const renderStatusButton = () => {
    const statusMap: Record<string, { bg: string, text: string, border: string, label: string, icon: React.ReactNode, title: string, onClick: () => void }> = {
      READY: {
        bg: "bg-emerald-500/10 hover:bg-emerald-500/20", text: "text-emerald-400", border: "border-emerald-500/20",
        label: t('readyOk'), icon: <CheckCircle size={14} />, title: t('pressToProvision') || "Press to provision",
        onClick: () => onInstantProvision(node)
      },
      RESTORED: {
        bg: "bg-indigo-500/10 hover:bg-indigo-500/20", text: "text-indigo-400", border: "border-indigo-500/30",
        label: "RESTORED", icon: <CheckCircle size={14} />, title: t('needsLicenseUpdate') || "Needs License Update",
        onClick: () => onShowDetails(node.id)
      },
      NEEDS_FIX: {
        bg: "bg-amber-500/10 hover:bg-amber-500/20", text: "text-amber-400", border: "border-amber-500/20",
        label: t('needsFixPrepare'), icon: <AlertTriangle size={14} />, title: t('pressToProvision') || "Press to provision",
        onClick: () => onInstantProvision(node)
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
        className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold border transition-colors cursor-pointer whitespace-nowrap ${config.bg} ${config.text} ${config.border} ${node.status === 'RESTORED' ? 'shadow-[0_0_8px_rgba(99,102,241,0.5)]' : ''}`}
        title={config.title}
      >
        {config.icon} {config.label}
      </button>
    );
  };

  return (
    <tr className="hover:bg-zinc-800/30 transition-colors">
      {bulkDeleteMode && (
        <td className="px-3.5 py-2.5 w-10 text-center">
          <input
            type="checkbox"
            checked={isSelected}
            onChange={(e) => onSelectNode(node.id, e.target.checked)}
            className="rounded border-zinc-800 bg-zinc-950 text-indigo-600 focus:ring-indigo-500 h-4 w-4 cursor-pointer"
          />
        </td>
      )}
      <td className="px-3.5 py-2.5 font-semibold text-zinc-50 flex items-center gap-2" style={{ paddingLeft: `${depth * 20 + 20}px` }}>
        <Cpu size={14} className="text-zinc-500 shrink-0" />
        <div className="flex flex-col min-w-0">
          <span className="truncate" title={node.hostname}>{node.hostname}</span>
          <div className="text-[11px] text-zinc-400 flex items-center gap-1.5 font-normal leading-none mt-1 whitespace-nowrap">
            <span className="text-indigo-400/90 font-semibold">
              {t('groupLabel') || 'Group'}: {groupName || '—'}
            </span>
            <span className="text-zinc-600">•</span>
            <span className="text-zinc-400 font-mono text-[10px] bg-zinc-800/80 px-1.5 py-0.5 rounded border border-zinc-700/50">
              {effectiveLimitStr}
            </span>
          </div>
          {(node.backup_paused || node.missed_window) && (
            <div className="flex gap-1 mt-1">
              {node.backup_paused && (
                <span className="px-1.5 py-0.5 bg-amber-500/10 text-amber-400 border border-amber-500/20 rounded text-[9px] font-bold">
                  {t('paused')}
                </span>
              )}
              {node.missed_window && (
                <span className="px-1.5 py-0.5 bg-rose-500/10 text-rose-400 border border-rose-500/20 rounded text-[9px] font-bold">
                  {t('missedWindow')}
                </span>
              )}
            </div>
          )}
        </div>
      </td>
      <td className="px-3.5 py-2.5 text-zinc-400 whitespace-nowrap text-xs">
        <span className={getIpColorClass()} title={getIpTooltip()}>
          {node.ip_address}
        </span>
        :{node.ssh_port}
      </td>
      <td className="px-3.5 py-2.5 text-zinc-300 font-medium text-xs whitespace-nowrap">{node.os_version || t('unknown')}</td>
      <td className="px-3.5 py-2.5 whitespace-nowrap">
        <div className="flex flex-col leading-tight">
          <span className="text-zinc-300 font-medium text-xs">{t('diskLabel')}: {node.disk_type ? node.disk_type.split(' ')[0] : 'UNKNOWN'}</span>
          <span className="text-zinc-500 text-[11px] mt-0.5">{t('netLabel')}: {node.network_iface || t('unknown').toUpperCase()}</span>
        </div>
      </td>
      <td className="px-3.5 py-2.5 whitespace-nowrap">{renderStatusButton()}</td>
      <td className="px-3.5 py-2.5 text-zinc-400 text-xs whitespace-nowrap">
        {node.last_backup ? formatDate(node.last_backup, timezone) : t('never')}
      </td>
      <td className="px-3.5 py-2 text-right whitespace-nowrap">
        <div className="inline-flex flex-col items-end gap-1.5 w-[140px]">
          {/* Top row: Node Details */}
          <button
            onClick={() => onShowDetails(node.id)}
            className="w-full text-center px-2 py-1 text-xs font-semibold bg-zinc-800 hover:bg-zinc-750 text-zinc-200 border border-zinc-700/80 rounded hover:text-indigo-400 hover:border-zinc-600 transition-colors cursor-pointer"
          >
            {t('nodeDetails')}
          </button>
          {/* Bottom row: Backup + Delete */}
          <div className="flex items-center gap-1.5 w-full">
            <button
              onClick={() => onShowBackup(node)}
              disabled={node.status !== 'READY' && !node.is_backup_running}
              title={liveSpeed ? t('currentSpeedLabel') : undefined}
              className={`flex-1 flex items-center justify-center gap-1 px-2 py-1 text-xs font-semibold rounded border transition-colors ${
                node.is_backup_running
                  ? 'text-indigo-300 border-indigo-500 bg-indigo-500/10 hover:bg-indigo-500/20 cursor-pointer font-bold animate-pulse'
                  : 'bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-400 border-indigo-500/20 disabled:opacity-30 cursor-pointer'
              }`}
            >
              {/* last_backup is set only alongside a SUCCESS archive and
                  cleared when the node's archives are purged, so it answers
                  "is there something to restore from" without another query.
                  Retention never prunes a node's newest archive, so it does
                  not go stale behind our back. */}
              {node.last_backup && (
                <CheckCircle
                  size={12}
                  className="shrink-0 text-emerald-400"
                  aria-label={t('hasRestorableArchive')}
                />
              )}
              {/* A measured rate, not a share of the whole: borg does not
                  report how much is left, so there is no honest percentage
                  to draw. The pulse carries "still going" instead. */}
              <span className="truncate">
                {node.is_backup_running && liveSpeed
                  ? `${t('backupAction')} (${liveSpeed})`
                  : t('backupAction')}
              </span>
            </button>
            <button
              onClick={() => onDeleteNode(node.id, node.hostname)}
              className="p-1 bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 rounded border border-rose-500/20 transition-colors cursor-pointer shrink-0"
              title={t('deleteNodeTooltip')}
            >
              <Trash2 size={13} />
            </button>
          </div>
        </div>
      </td>
    </tr>
  );
}

/**
 * Memoised because FleetTab re-renders every five seconds and a fleet is
 * thousands of rows.
 *
 * The poll returns the same data almost every time, so without this each tick
 * rebuilds and re-reconciles the entire table for nothing. For the memo to
 * hold, every prop has to be stable across renders that changed nothing —
 * which is why this component takes `isSelected` rather than the selection
 * map, and an `onShowDetails` that takes an id rather than a closure baked per
 * row. Reintroducing either would leave React.memo in place and doing nothing,
 * which is worse than not having it: it reads as solved.
 */
export const NodeRow = React.memo(NodeRowComponent);

