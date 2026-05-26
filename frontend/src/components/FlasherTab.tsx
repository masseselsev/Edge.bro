import React, { useState, useEffect, useRef } from 'react';
import { AlertTriangle, Server, HardDrive, RefreshCw, Play, Terminal as TermIcon, FileText } from 'lucide-react';

interface Device {
  name: string;
  size: number;
  model: string;
  rotational: boolean;
  disk_type: string;
  is_usb?: boolean;
}

interface Node {
  id: number;
  hostname: string;
  disk_type: string;
  efi_uuid: string | null;
}

interface Snapshot {
  id: number;
  archive_name: string;
  timestamp: string;
  original_size: number;
}

interface FlasherTabProps {
  onViewLogs: (taskId: string, title: string) => void;
}

export default function FlasherTab({ onViewLogs }: FlasherTabProps) {
  const [devices, setDevices] = useState<Device[]>([]);
  const [nodes, setNodes] = useState<Node[]>([]);
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
  const [wipeMacBindings, setWipeMacBindings] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  const fetchDevices = async () => {
    setScanning(true);
    try {
      const res = await fetch('/api/scanner/devices');
      const data = await res.json();
      setDevices(data);
    } catch (e) {
      console.error(e);
    } finally {
      setScanning(false);
    }
  };

  const fetchNodes = async () => {
    try {
      const res = await fetch('/api/nodes');
      const data = await res.json();
      setNodes(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoadingNodes(false);
    }
  };

  const fetchSnapshots = async (nodeId: number) => {
    setLoadingSnapshots(true);
    try {
      const res = await fetch(`/api/nodes/${nodeId}/history`);
      const data = await res.json();
      // Filter for successful backups
      setSnapshots(data.filter((h: any) => h.status === 'SUCCESS'));
    } catch (e) {
      console.error(e);
    } finally {
      setLoadingSnapshots(false);
    }
  };

  useEffect(() => {
    fetchDevices();
    fetchNodes();
  }, []);

  useEffect(() => {
    if (selectedNodeId) {
      fetchSnapshots(Number(selectedNodeId));
      setSelectedSnapshot('');
    } else {
      setSnapshots([]);
    }
  }, [selectedNodeId]);

  // Handle mismatch check
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

      // Trigger log viewing immediately
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

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      {/* Configuration & Trigger form */}
      <div className="lg:col-span-2 space-y-6">
        <div className="p-6 bg-zinc-900 border border-zinc-800 rounded-2xl space-y-4">
          <div>
            <h3 className="text-lg font-bold text-white flex items-center gap-2"><Play size={18} className="text-indigo-400" /> Bare-Metal Flasher</h3>
            <p className="text-xs text-zinc-400">Configure target snapshot extraction and format target physical devices.</p>
          </div>

          <form onSubmit={handleStartFlash} className="space-y-4">
            <div>
              <label className="block text-xs font-semibold text-zinc-400 mb-1.5">1. Select Target Node (Data Source)</label>
              <select
                value={selectedNodeId}
                onChange={(e) => setSelectedNodeId(e.target.value ? Number(e.target.value) : '')}
                className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-white text-sm focus:border-indigo-500 focus:outline-none"
              >
                <option value="">-- Choose Node --</option>
                {nodes.map(n => (
                  <option key={n.id} value={n.id}>
                    {n.hostname} (Original Disk: {n.disk_type}) {n.efi_uuid ? '' : '[NO EFI UUID]'}
                  </option>
                ))}
              </select>
              {selectedNode && !selectedNode.efi_uuid && (
                <div className="mt-1 text-xs text-rose-400 flex items-center gap-1">
                  <AlertTriangle size={12} /> Auto-Prepare has not been run on this node. Bare-metal restore is locked.
                </div>
              )}
            </div>

            {selectedNodeId && (
              <div>
                <label className="block text-xs font-semibold text-zinc-400 mb-1.5">2. Select Backup Snapshot</label>
                <select
                  value={selectedSnapshot}
                  onChange={(e) => setSelectedSnapshot(e.target.value)}
                  className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-white text-sm focus:border-indigo-500 focus:outline-none"
                >
                  <option value="">-- Choose Archive --</option>
                  {snapshots.map(s => (
                    <option key={s.id} value={s.archive_name}>
                      {s.archive_name} ({getFormatSize(s.original_size)})
                    </option>
                  ))}
                </select>
              </div>
            )}

            <div>
              <label className="block text-xs font-semibold text-zinc-400 mb-1.5">3. Select Target Flash Block Device</label>
              <select
                value={selectedDevice}
                onChange={(e) => setSelectedDevice(e.target.value)}
                className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-white text-sm focus:border-indigo-500 focus:outline-none"
              >
                <option value="">-- Choose Physical Target --</option>
                {devices.map(d => (
                  <option key={d.name} value={d.name}>
                    {d.name} - {d.model} ({getFormatSize(d.size)} - {d.disk_type} {d.rotational ? 'HDD' : 'SSD'}{d.is_usb ? ' [USB]' : ''})
                  </option>
                ))}
              </select>
            </div>

            {/* Mismatch warnings */}
            {mismatchWarning && (
              <div className="p-4 bg-amber-500/10 border border-amber-500/20 rounded-xl space-y-2">
                <div className="flex items-start gap-2.5">
                  <AlertTriangle className="text-amber-400 mt-0.5 shrink-0" size={18} />
                  <div>
                    <h4 className="text-sm font-bold text-amber-400">HARDWARE TYPE MISMATCH WARNING</h4>
                    <p className="text-xs text-zinc-300">
                      The original node was provisioned with a **{selectedNode?.disk_type}** device. 
                      You have selected a **{devices.find(d => d.name === selectedDevice)?.disk_type}** drive. 
                      Restoring to mismatched architectures could lead to boot loader PCIe driver errors.
                    </p>
                  </div>
                </div>
                <label className="flex items-center gap-2 mt-2 px-3 py-2 bg-amber-500/5 border border-amber-500/10 rounded-lg cursor-pointer">
                  <input
                    type="checkbox"
                    checked={overrideChecked}
                    onChange={(e) => setOverrideChecked(e.target.checked)}
                    className="rounded bg-zinc-950 border-zinc-800 text-indigo-600 focus:ring-0"
                  />
                  <span className="text-xs font-semibold text-amber-400">I explicitly confirm this restore mismatch and assume boot risks</span>
                </label>
              </div>
            )}
            {/* Network configuration restore options */}
            <div className="p-4 bg-zinc-950 border border-zinc-800 rounded-xl space-y-3">
              <h4 className="text-xs font-bold text-zinc-300 uppercase tracking-wider">Network Settings</h4>
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
                    <span className="text-xs font-semibold text-zinc-200">Preserve original network configurations (1-to-1)</span>
                    <span className="text-[10px] text-zinc-400">Keep static IPs and interfaces. Skip DHCP override fallback.</span>
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
                    <span className="text-xs font-semibold text-zinc-200">Reset MAC address bindings (for new motherboards)</span>
                    <span className="text-[10px] text-zinc-400">Wipes persistent udev rules so interface names bind dynamically.</span>
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
              {submitting ? 'Wiping and Flashing...' : 'START FLASHING (DANGER ZONE)'}
            </button>
          </form>
        </div>
      </div>

      {/* Local scanning status */}
      <div className="space-y-6">
        <div className="p-6 bg-zinc-900 border border-zinc-800 rounded-2xl space-y-4">
          <div className="flex justify-between items-center">
            <h3 className="text-sm font-bold text-white flex items-center gap-2"><HardDrive size={16} /> Device Scanner</h3>
            <button
              onClick={fetchDevices}
              disabled={scanning}
              className="p-1 hover:bg-zinc-800 text-zinc-400 hover:text-white rounded transition-colors"
            >
              <RefreshCw size={14} className={scanning ? 'animate-spin' : ''} />
            </button>
          </div>
          <div className="space-y-2 max-h-80 overflow-y-auto">
            {devices.length === 0 ? (
              <div className="text-center py-8 text-xs text-zinc-500">No external drives found. Make sure SATA/NVMe targets are plugged in.</div>
            ) : (
              devices.map(d => (
                <div key={d.name} className="p-3 bg-zinc-950 border border-zinc-800/80 rounded-xl space-y-1">
                  <div className="flex justify-between text-xs font-bold text-white">
                    <span className="flex items-center gap-1.5">
                      {d.name}
                      {d.is_usb && (
                        <span className="px-1.5 py-0.5 text-[9px] bg-amber-500/10 text-amber-400 border border-amber-500/20 rounded font-semibold uppercase tracking-wider">
                          USB
                        </span>
                      )}
                    </span>
                    <span className="text-indigo-400">{d.disk_type}</span>
                  </div>
                  <div className="text-[11px] text-zinc-400 flex justify-between">
                    <span>Model: {d.model}</span>
                    <span>{getFormatSize(d.size)}</span>
                  </div>
                  <div className="text-[10px] text-zinc-500">
                    Type: {d.rotational ? 'Rotational HDD' : 'Solid State Drive (SSD)'}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
