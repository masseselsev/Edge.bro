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
}

interface EdgeNode {
  id: number;
  hostname: string;
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
  comment: string | null;
}

import { formatDate } from './dateUtils';
import { SearchableSelect } from './SearchableSelect';
import type { Option } from './SearchableSelect';

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
  
  const [scanning, setScanning] = useState(false);
  const [loadingNodes, setLoadingNodes] = useState(true);
  const [loadingSnapshots, setLoadingSnapshots] = useState(false);
  const [mismatchWarning, setMismatchWarning] = useState(false);
  const [overrideChecked, setOverrideChecked] = useState(false);
  const [keepNetworkConfigs, setKeepNetworkConfigs] = useState(true);
  const [wipeMacBindings, setWipeMacBindings] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  // Sync states
  const [syncing, setSyncing] = useState(false);
  const [syncTaskId, setSyncTaskId] = useState<string | null>(null);
  const [syncProgress, setSyncProgress] = useState(0);

  // Storage partition capacity state
  const [storageInfo, setStorageInfo] = useState<{
    total: number;
    used: number;
    free: number;
    path: string;
    is_mounted: boolean;
    potential_paths?: string[];
  } | null>(null);

  const handleSyncToUsb = async () => {
    if (!selectedNodeId) return;
    const node = nodes.find(n => n.id === selectedNodeId);
    if (!node) return;
    
    setSyncing(true);
    setSyncProgress(0);
    try {
      const res = await fetch(`/api/kiosk/sync/${node.hostname}`, { method: 'POST' });
      if (!res.ok) throw new Error("Failed to start sync");
      const data = await res.json();
      if (data.task_id) {
        setSyncTaskId(data.task_id);
      }
    } catch (err: any) {
      alert(`Sync failed to start: ${err.message}`);
      setSyncing(false);
    }
  };

  useEffect(() => {
    if (!syncTaskId) return;

    let intervalId: any = null;
    const pollStatus = async () => {
      try {
        const res = await fetch(`/api/tasks/${syncTaskId}`);
        if (!res.ok) return;
        const data = await res.json();
        if (data.progress) {
          setSyncProgress(data.progress);
        }
        if (data.status === 'SUCCESS') {
          clearInterval(intervalId);
          setSyncTaskId(null);
          setSyncing(false);
          alert(t('settingsSuccess'));
          // Refresh lists to see cached nodes
          fetchDevices();
          fetchNodes();
          fetchStorageInfo();
        } else if (data.status === 'FAILED') {
          clearInterval(intervalId);
          setSyncTaskId(null);
          setSyncing(false);
          alert(t('failed'));
        }
      } catch (err) {
        console.error(err);
      }
    };

    pollStatus();
    intervalId = setInterval(pollStatus, 2000);
    return () => clearInterval(intervalId);
  }, [syncTaskId]);

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
      const res = await fetch('/api/nodes');
      if (res.ok) {
        const data = await res.json();
        setNodes(Array.isArray(data) ? data : []);
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
      const res = await fetch(`/api/nodes/${nodeId}/history`);
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data)) {
          setSnapshots(data.filter((h: any) => h.status === 'SUCCESS'));
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
    fetchDevices();
    fetchNodes();
    if (isKiosk) {
      fetchStorageInfo();
    }
  }, [isKiosk]);

  useEffect(() => {
    if (selectedNodeId) {
      fetchSnapshots(Number(selectedNodeId));
      setSelectedSnapshot('');
    } else {
      setSnapshots([]);
    }
  }, [selectedNodeId]);

  useEffect(() => {
    if (selectedNodeId && selectedDevice) {
      const node = nodes.find(n => n.id === Number(selectedNodeId));
      const device = devices.find(d => d.name === selectedDevice);
      if (node && device) {
        const isMismatch = node.disk_type !== 'UNKNOWN' && node.disk_type !== device.disk_type;
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
          wipe_mac_bindings: wipeMacBindings
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

  const getFormatSize = (bytes: number) => {
    if (bytes === 0) return '0 B';
    const g = bytes / (1024 * 1024 * 1024);
    return `${g.toFixed(1)} GB`;
  };

  const selectedNode = nodes.find(n => n.id === Number(selectedNodeId));

  // Options converters
  const nodeOptions = nodes
    .filter(n => n.last_backup !== null)
    .map(n => ({
      value: n.id,
      label: n.hostname,
      sublabel: `Original Disk: ${n.disk_type}${n.efi_uuid ? '' : ' [NO EFI UUID]'}${n.repo_size_bytes !== undefined ? ` — Repo Size: ${getFormatSize(n.repo_size_bytes)}` : ''}`,
      disabled: false
    }));

  const snapshotOptions = snapshots.map(s => ({
    value: s.archive_name,
    label: s.archive_name,
    sublabel: `${formatDate(s.timestamp, timezone)} (${getFormatSize(s.original_size)})${s.comment ? ` — ${s.comment}` : ''}`,
    disabled: false
  }));

  const deviceOptions = devices.map(d => ({
    value: d.name,
    label: d.name,
    sublabel: `${d.model} (${getFormatSize(d.size)} - ${d.disk_type} ${d.rotational ? 'HDD' : 'SSD'}${d.is_usb ? ' [USB]' : ''})`,
    disabled: false
  }));

  const isOnlineWaitingApproval = isKiosk && restoreMode === 'online' && kioskStatus !== 'APPROVED';

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 relative">
      {isOnlineWaitingApproval && (
        <div className="absolute inset-0 z-30 bg-zinc-950/70 backdrop-blur-sm rounded-3xl flex flex-col items-center justify-center p-6 text-center animate-fade-in border border-zinc-800/50">
          <div className="max-w-md space-y-4">
            <div className="inline-flex p-4 bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 rounded-2xl">
              <Loader2 size={32} className="animate-spin" />
            </div>
            <h3 className="text-lg font-bold text-zinc-150">Waiting for Server Approval</h3>
            <p className="text-xs text-zinc-400 leading-relaxed font-medium">
              This kiosk is waiting to connect to the server. You can configure the network using the indicator in the header, or toggle to Offline Mode to restore from local USB storage.
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

            {selectedNodeId && isKiosk && restoreMode === 'online' && (
              <div className="p-4 bg-indigo-50/50 dark:bg-indigo-950/20 border border-indigo-100 dark:border-indigo-900/30 rounded-xl space-y-3">
                <div className="flex items-center justify-between">
                  <div>
                    <h4 className="text-xs font-bold text-indigo-800 dark:text-indigo-300 uppercase tracking-wider">{t('localBackupStorage')}</h4>
                    <p className="text-[10px] text-zinc-400 mt-1">
                      {t('offlineCapabilitiesText')}
                    </p>
                    {storageInfo && (
                      <p className="text-[10px] text-zinc-500 mt-1.5 flex items-center gap-1.5 font-semibold">
                        <HardDrive size={11} className="text-zinc-400" />
                        {t('freeSpace')}: <span className="text-emerald-400">{getFormatSize(storageInfo.free)}</span> / {getFormatSize(storageInfo.total)}
                      </p>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={handleSyncToUsb}
                    disabled={syncing}
                    className="flex items-center gap-1.5 px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded-lg text-xs font-semibold transition-colors cursor-pointer"
                  >
                    <Download size={13} />
                    {syncing ? t('syncingText') : t('syncButton')}
                  </button>
                </div>
                {syncing && (
                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between text-[10px] font-mono text-zinc-400">
                      <span>Syncing files...</span>
                      <span>{syncProgress}%</span>
                    </div>
                    <div className="w-full bg-zinc-950 h-1.5 rounded-full overflow-hidden border border-zinc-800">
                      <div 
                        className="bg-indigo-500 h-full rounded-full transition-all duration-300"
                        style={{ width: `${syncProgress}%` }}
                      />
                    </div>
                  </div>
                )}
              </div>
            )}

            {selectedNodeId && (
              <div>
                <label className="block text-xs font-semibold text-zinc-400 mb-1.5">2. Select Backup Snapshot</label>
                <SearchableSelect
                  options={snapshotOptions}
                  value={selectedSnapshot}
                  onChange={(val) => setSelectedSnapshot(val)}
                  placeholder="-- Choose Snapshot Archive --"
                  disabled={loadingSnapshots}
                />
              </div>
            )}

            <div>
              <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('selectDisk')}</label>
              <SearchableSelect
                options={deviceOptions}
                value={selectedDevice}
                onChange={(val) => setSelectedDevice(val)}
                placeholder={t('selectDiskPlaceholder')}
                disabled={scanning}
              />
            </div>

            {/* Mismatch warnings */}
            {mismatchWarning && (
              <div className="p-4 bg-amber-500/10 border border-amber-500/20 rounded-xl space-y-2">
                <div className="flex items-start gap-2.5">
                  <AlertTriangle className="text-amber-400 mt-0.5 shrink-0" size={18} />
                  <div>
                    <h4 className="text-sm font-bold text-amber-400">{t('flashWarningTitle')}</h4>
                    <p className="text-xs text-zinc-300">
                      {t('flashWarningText', { dev: selectedDevice, snapshot: selectedSnapshot, node: selectedNode?.hostname || '' })}
                    </p>
                  </div>
                </div>
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
        getFormatSize={getFormatSize}
        onStoragePathChange={handleStoragePathChange}
      />
    </div>
  );
}
