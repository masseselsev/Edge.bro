import React, { useState, useEffect, useRef } from 'react';
import { AlertTriangle, Server, HardDrive, Play, Download, Loader2 } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';
import { DeviceScannerSection } from './DeviceScannerSection';

interface Device {
  name: string;
  size: number;
  model: string;
  rotational: boolean;
  disk_type: string;
  is_usb?: boolean;
  is_system?: boolean;
}

interface EdgeNode {
  id: number;
  hostname: string;
  ip_address: string;
  notes: string | null;
  disk_type: string;
  efi_uuid: string | null;
  last_backup: string | null;
  repo_size_bytes?: number;
}

interface Snapshot {
  id: number;
  archive_name: string;
  timestamp: string;
  original_size: number;
  deduplicated_size: number;
  comment: string | null;
}

import { formatDate } from './dateUtils';
import { SearchableSelect } from './SearchableSelect';
import type { Option } from './SearchableSelect';
import { formatBytes, downloadSizeBytes } from './formatBytes';

interface FlasherTabProps {
  onViewLogs: (taskId: string, title: string) => void;
  timezone?: string;
  restoreMode?: 'offline' | 'online';
  isKiosk?: boolean;
  kioskStatus?: string;
}

export default function FlasherTab({ onViewLogs, timezone, restoreMode = 'offline', isKiosk = false, kioskStatus = 'APPROVED' }: FlasherTabProps) {
  const { t } = useTranslation();
  const [devices, setDevices] = useState<Device[]>([]);
  const [nodes, setNodes] = useState<EdgeNode[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<number | ''>('');
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [selectedSnapshot, setSelectedSnapshot] = useState<string>('');
  const [selectedDevice, setSelectedDevice] = useState<string>('');
  const [systemDiskPending, setSystemDiskPending] = useState<Device | null>(null);
  const [systemDiskConfirmed, setSystemDiskConfirmed] = useState(false);
  
  const [scanning, setScanning] = useState(false);
  const [loadingNodes, setLoadingNodes] = useState(true);
  const [loadingSnapshots, setLoadingSnapshots] = useState(false);
  const [mismatchWarning, setMismatchWarning] = useState(false);
  const [overrideChecked, setOverrideChecked] = useState(false);
  const [keepNetworkConfigs, setKeepNetworkConfigs] = useState(true);
  const [wipeMacBindings, setWipeMacBindings] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  // Storage partition capacity state
  const [storageInfo, setStorageInfo] = useState<{
    total: number;
    used: number;
    free: number;
    path: string;
    is_mounted: boolean;
    potential_paths?: string[];
  } | null>(null);

  const fetchDevices = async () => {
    setScanning(true);
    try {
      const res = await fetch('/api/scanner/devices');
      if (res.ok) {
        const data = await res.json();
        setDevices(Array.isArray(data) ? data : []);
      } else {
        setDevices([]);
      }
    } catch (e) {
      console.error(e);
      setDevices([]);
    } finally {
      setScanning(false);
    }
  };

  const fetchNodes = async () => {
    try {
      const res = await fetch(`/api/nodes?mode=${restoreMode}`);
      if (res.ok) {
        const data = await res.json();
        setNodes(Array.isArray(data) ? data : (data.nodes || []));
      } else {
        setNodes([]);
      }
    } catch (e) {
      console.error(e);
      setNodes([]);
    } finally {
      setLoadingNodes(false);
    }
  };

  const fetchStorageInfo = async () => {
    if (!isKiosk) return;
    try {
      const res = await fetch('/api/kiosk/storage');
      if (res.ok) {
        const data = await res.json();
        setStorageInfo(data);
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
        fetchNodes();
      } else {
        const err = await res.json();
        alert(`Failed to set storage path: ${err.detail || 'Unknown error'}`);
      }
    } catch (e: any) {
      alert(`Error updating storage path: ${e.message}`);
    }
  };

  const fetchSnapshots = async (nodeId: number) => {
    setLoadingSnapshots(true);
    try {
      const res = await fetch(`/api/nodes/${nodeId}/history?mode=${restoreMode}`);
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data)) {
          const filtered = data.filter((h: any) => h.status === 'SUCCESS');
          filtered.sort((a: any, b: any) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
          setSnapshots(filtered);
        } else {
          setSnapshots([]);
        }
      } else {
        setSnapshots([]);
      }
    } catch (e) {
      console.error(e);
      setSnapshots([]);
    } finally {
      setLoadingSnapshots(false);
    }
  };

  useEffect(() => {
    setSelectedNodeId('');
    setSelectedSnapshot('');
    setSnapshots([]);
  }, [restoreMode]);

  useEffect(() => {
    fetchDevices();
    fetchNodes();
    if (isKiosk) {
      fetchStorageInfo();
    }
  }, [isKiosk, restoreMode]);

  useEffect(() => {
    if (selectedNodeId) {
      fetchSnapshots(Number(selectedNodeId));
      setSelectedSnapshot('');
    } else {
      setSnapshots([]);
    }
  }, [selectedNodeId, restoreMode]);

  useEffect(() => {
    if (selectedNodeId && selectedDevice) {
      const node = nodes.find(n => n.id === Number(selectedNodeId));
      const device = devices.find(d => d.name === selectedDevice);
      if (node && device) {
        const nodeBaseType = node.disk_type.toUpperCase().startsWith('NVME') ? 'NVME' : (node.disk_type.toUpperCase().startsWith('SATA') ? 'SATA' : 'UNKNOWN');
        const isMismatch = nodeBaseType !== 'UNKNOWN' && nodeBaseType !== device.disk_type;
        setMismatchWarning(isMismatch);
        if (!isMismatch) {
          setOverrideChecked(false);
        }
      }
    } else {
      setMismatchWarning(false);
      setOverrideChecked(false);
    }
  }, [selectedNodeId, selectedDevice, nodes, devices]);

  const handleStartFlash = async (e: React.FormEvent) => {
    e.preventDefault();
    if (mismatchWarning && !overrideChecked) {
      setError('You must explicitly confirm you want to proceed with disk mismatch.');
      return;
    }

    setSubmitting(true);
    setError('');

    try {
      const res = await fetch('/api/restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          node_id: Number(selectedNodeId),
          archive_name: selectedSnapshot,
          target_dev: selectedDevice,
          override_mismatch: overrideChecked,
          keep_network_configs: keepNetworkConfigs,
          wipe_mac_bindings: wipeMacBindings,
          restore_mode: restoreMode
        })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to trigger restore');

      if (data.task_id) {
        onViewLogs(data.task_id, `Restore Flashing on ${selectedDevice}`);
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  };

  const selectedNode = nodes.find(n => n.id === Number(selectedNodeId));

  // Options converters
  // Sublabel stays a plain string so SearchableSelect can match IP and notes when filtering.
  const nodeOptions = nodes
    .filter(n => n.last_backup !== null)
    .map(n => ({
      value: n.id,
      label: n.hostname,
      sublabel: [
        n.ip_address,
        `Original Disk: ${n.disk_type}${n.efi_uuid ? '' : ' [NO EFI UUID]'}`,
        ...(n.repo_size_bytes !== undefined ? [`Repo Size: ${formatBytes(n.repo_size_bytes)}`] : []),
        ...(n.notes?.trim() ? [n.notes.trim()] : []),
      ].filter(Boolean).join(' — '),
      disabled: false
    }));

  const snapshotOptions = snapshots.map(s => {
    let sizeDetails = "";
    if (restoreMode === 'online') {
      sizeDetails = `${formatBytes(downloadSizeBytes(s))} ${t('estDownload') || 'to download'}`;
    } else {
      sizeDetails = `${formatBytes(s.original_size)} ${t('sizeOnDisk') || 'on disk'} / ${formatBytes(s.deduplicated_size)} ${t('archiveSize') || 'archive'}`;
    }

    let label = s.archive_name;
    if (selectedNode && label.startsWith(selectedNode.hostname + "-")) {
      label = label.substring(selectedNode.hostname.length + 1);
    }

    const sublabel = (
      <span>
        {formatDate(s.timestamp, timezone)} ({sizeDetails})
        {s.comment && (
          <>
            {" — "}
            <strong className="text-zinc-200 font-bold">{s.comment}</strong>
          </>
        )}
      </span>
    );

    return {
      value: s.archive_name,
      label,
      sublabel,
      disabled: false
    };
  });

  const deviceOptions = devices.map(d => ({
    value: d.name,
    label: d.name,
    sublabel: `${d.model} (${formatBytes(d.size)} - ${d.disk_type} ${d.rotational ? 'HDD' : 'SSD'}${d.is_system ? ' [SYSTEM]' : (d.is_usb ? ' [USB]' : '')})`,
    disabled: false
  }));

  const handleDeviceChange = (devName: string | number) => {
    const val = String(devName || '');
    if (!val) {
      setSelectedDevice('');
      return;
    }
    const dev = devices.find(d => d.name === val);
    if (dev?.is_system) {
      setSystemDiskPending(dev);
      setSystemDiskConfirmed(false);
    } else {
      setSelectedDevice(val);
    }
  };

  const isOnlineWaitingApproval = isKiosk && restoreMode === 'online' && kioskStatus !== 'APPROVED';

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 relative">
      {systemDiskPending && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-fade-in">
          <div className="w-full max-w-md p-6 bg-zinc-900 border-2 border-rose-500/50 rounded-2xl shadow-2xl space-y-4 animate-modal-in">
            <div className="flex items-start gap-3 border-b border-zinc-800 pb-3">
              <div className="p-2.5 bg-rose-500/20 text-rose-400 border border-rose-500/30 rounded-xl shrink-0">
                <AlertTriangle size={24} className="animate-pulse" />
              </div>
              <div>
                <h3 className="text-sm font-black text-rose-400 uppercase tracking-tight leading-snug">
                  {t('systemDiskWarningTitle') || 'ARE YOU SURE YOU WANT TO OVERWRITE THIS SYSTEM DISK?!'}
                </h3>
                <p className="text-[11px] text-zinc-400 mt-1 leading-relaxed">
                  {t('systemDiskWarningDesc') || 'This drive is an internal system disk of the host device (laptop/PC). Writing to it will completely erase the host operating system and all local partitions!'}
                </p>
              </div>
            </div>

            <div className="p-3 bg-zinc-950 border border-rose-500/20 rounded-xl space-y-1 text-xs">
              <div className="flex justify-between font-mono font-bold text-zinc-200">
                <span>{systemDiskPending.name}</span>
                <span className="text-rose-400 font-semibold uppercase">{systemDiskPending.disk_type}</span>
              </div>
              <div className="flex justify-between text-[11px] text-zinc-400">
                <span>Model: {systemDiskPending.model}</span>
                <span>{formatBytes(systemDiskPending.size)}</span>
              </div>
            </div>

            <label className="flex items-start gap-2.5 p-3 bg-rose-500/10 border border-rose-500/20 rounded-xl cursor-pointer">
              <input
                type="checkbox"
                checked={systemDiskConfirmed}
                onChange={(e) => setSystemDiskConfirmed(e.target.checked)}
                className="mt-0.5 rounded border-zinc-700 text-rose-600 focus:ring-rose-500 bg-zinc-900 cursor-pointer"
              />
              <span className="text-xs font-semibold text-rose-200 leading-snug">
                {t('systemDiskConfirmCheckbox') || 'Yes, I understand the risks and confirm selecting this system disk'}
              </span>
            </label>

            <div className="flex justify-end gap-2 pt-2 border-t border-zinc-800">
              <button
                type="button"
                onClick={() => {
                  setSystemDiskPending(null);
                  setSystemDiskConfirmed(false);
                }}
                className="px-4 py-2 text-xs font-semibold text-zinc-400 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors cursor-pointer"
              >
                {t('cancelSelection') || 'Cancel'}
              </button>
              <button
                type="button"
                disabled={!systemDiskConfirmed}
                onClick={() => {
                  setSelectedDevice(systemDiskPending.name);
                  setSystemDiskPending(null);
                  setSystemDiskConfirmed(false);
                }}
                className="px-4 py-2 text-xs font-bold text-white bg-rose-600 hover:bg-rose-500 rounded-lg disabled:opacity-40 disabled:cursor-not-allowed transition-colors cursor-pointer shadow-lg shadow-rose-950/50"
              >
                {t('confirmSelectSystemDisk') || 'Confirm System Disk Selection'}
              </button>
            </div>
          </div>
        </div>
      )}

      {isOnlineWaitingApproval && (
        <div className="absolute inset-0 z-30 bg-zinc-950/45 backdrop-blur-[2px] rounded-3xl flex items-center justify-center p-6 text-center animate-fade-in border border-zinc-800/30">
          <div className="max-w-xs p-5 bg-zinc-900/90 border border-zinc-800 rounded-2xl shadow-xl space-y-2">
            <h3 className="text-sm font-bold text-zinc-200">{t('flasherLockedTitle') || 'Flasher Locked'}</h3>
            <p className="text-[11px] text-zinc-400 leading-normal">
              {t('flasherLockedText') || 'Restore controls are disabled in Online Mode until this kiosk is authorized by the orchestrator.'}
            </p>
          </div>
        </div>
      )}
      {/* Configuration & Trigger form */}
      <div className="lg:col-span-2 space-y-6">
        <div className="p-6 bg-zinc-900 border border-zinc-800 rounded-2xl space-y-4">
          <div>
            <h3 className="text-lg font-bold text-zinc-50 flex items-center gap-2"><Play size={18} className="text-indigo-400" /> {t('flasherTitle')}</h3>
            <p className="text-xs text-zinc-400">{t('flasherSub')}</p>
          </div>

          <form onSubmit={handleStartFlash} className="space-y-4">
            <div>
              <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('selectNode')}</label>
              <SearchableSelect
                options={nodeOptions}
                value={selectedNodeId}
                onChange={(val) => setSelectedNodeId(val)}
                placeholder={t('selectNodePlaceholder')}
                disabled={loadingNodes}
              />
              {selectedNode && !selectedNode.efi_uuid && (
                <div className="mt-1.5 text-xs text-rose-400 flex items-center gap-1.5">
                  <AlertTriangle size={12} /> Auto-Prepare has not been run on this node. Bare-metal restore is locked.
                </div>
              )}
            </div>



            {selectedNodeId && (
              <div>
                <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('selectSnapshotStep') || '2. Select Backup Snapshot'}</label>
                <SearchableSelect
                  options={snapshotOptions}
                  value={selectedSnapshot}
                  onChange={(val) => setSelectedSnapshot(val)}
                  placeholder={t('chooseSnapshotPlaceholder') || '-- Choose Snapshot Archive --'}
                  disabled={loadingSnapshots}
                />
              </div>
            )}

            <div>
              <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('selectDisk')}</label>
              <SearchableSelect
                options={deviceOptions}
                value={selectedDevice}
                onChange={handleDeviceChange}
                placeholder={t('selectDiskPlaceholder')}
                disabled={scanning}
              />
            </div>

            {/* Mismatch warnings */}
            {mismatchWarning && (
              <div className="p-4 bg-amber-500/10 border border-amber-500/20 rounded-xl space-y-3">
                <div className="flex items-start gap-2.5">
                  <AlertTriangle className="text-amber-400 mt-0.5 shrink-0" size={18} />
                  <div>
                    <h4 className="text-sm font-bold text-amber-400">{t('flashWarningTitle')}</h4>
                    <p className="text-xs text-zinc-300">
                      {t('flashWarningText', { dev: selectedDevice.replace(/^\/dev\//, ''), snapshot: selectedSnapshot, node: selectedNode?.hostname || '' })}
                    </p>
                  </div>
                </div>
                <label className="flex items-center gap-2.5 mt-1 cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={overrideChecked}
                    onChange={(e) => setOverrideChecked(e.target.checked)}
                    className="rounded bg-zinc-950 border-zinc-800 text-indigo-600 focus:ring-0"
                  />
                  <span className="text-xs font-semibold text-zinc-200">
                    {t('flashOverrideCheckbox')}
                  </span>
                </label>
              </div>
            )}

            {/* Network configuration restore options */}
            <div className="p-4 bg-zinc-950 border border-zinc-800 rounded-xl space-y-3">
              <h4 className="text-xs font-bold text-zinc-300 uppercase tracking-wider">{t('networkSettings')}</h4>
              <div className="space-y-2.5">
                <label className="flex items-start gap-2.5 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={keepNetworkConfigs}
                    onChange={(e) => {
                      setKeepNetworkConfigs(e.target.checked);
                      if (!e.target.checked) {
                        setWipeMacBindings(true);
                      }
                    }}
                    className="mt-0.5 rounded bg-zinc-900 border-zinc-800 text-indigo-600 focus:ring-0"
                  />
                  <div className="flex flex-col">
                    <span className="text-xs font-semibold text-zinc-200">{t('preserveConfigs')}</span>
                    <span className="text-[10px] text-zinc-400">{t('preserveConfigsSub')}</span>
                  </div>
                </label>

                <label className={`flex items-start gap-2.5 ${keepNetworkConfigs ? 'cursor-pointer opacity-100' : 'cursor-not-allowed opacity-50'}`}>
                  <input
                    type="checkbox"
                    checked={wipeMacBindings}
                    disabled={!keepNetworkConfigs}
                    onChange={(e) => setWipeMacBindings(e.target.checked)}
                    className="mt-0.5 rounded bg-zinc-900 border-zinc-800 text-indigo-600 focus:ring-0"
                  />
                  <div className="flex flex-col">
                    <span className="text-xs font-semibold text-zinc-200">{t('resetMacs')}</span>
                    <span className="text-[10px] text-zinc-400">{t('resetMacsSub')}</span>
                  </div>
                </label>
              </div>
            </div>

            {error && <div className="text-xs text-rose-400 bg-rose-500/10 border border-rose-500/20 p-3 rounded-lg">{error}</div>}

            <button
              type="submit"
              disabled={submitting || !selectedSnapshot || !selectedDevice || (mismatchWarning && !overrideChecked) || (selectedNode && !selectedNode.efi_uuid)}
              className="w-full py-2.5 bg-rose-600 hover:bg-rose-500 text-white rounded-lg font-bold text-sm tracking-wide shadow-lg disabled:opacity-30 disabled:hover:bg-rose-600 transition-colors"
            >
              {submitting ? t('saving') : t('startFlashing')}
            </button>
          </form>
        </div>
      </div>
      <DeviceScannerSection
        devices={devices}
        scanning={scanning}
        onRefreshDevices={fetchDevices}
        isKiosk={isKiosk}
        storageInfo={storageInfo}
        onStoragePathChange={handleStoragePathChange}
      />
    </div>
  );
}
