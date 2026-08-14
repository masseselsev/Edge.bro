import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api';
import type { BackupHistory, Node } from '../types';
import { downloadSizeBytes } from '../components/formatBytes';

/**
 * Copying archives from the orchestrator onto the kiosk's own USB storage.
 *
 * This is the whole reason the technician's kiosk exists: it is carried to a
 * site whose server is dead, and it can only restore what it brought with it.
 * So before leaving, the operator picks the archives to take and this pulls
 * them down.
 *
 * Extracted from HistoryTab, where roughly 250 of 1240 lines were this and it
 * was interleaved with the orchestrator's own history browsing — two features
 * sharing one component because they happen to display the same rows.
 *
 * Inert when `isKiosk` is false. The orchestrator has the repository already.
 */

export interface SelectionMetrics {
  /** What the selected archives restore to, uncompressed. */
  totalOriginal: number;
  /** Roughly what will cross the wire. See estimateSelection. */
  totalEstimatedDownload: number;
}

/** `/api/kiosk/storage` — the volume archives are copied onto. */
export interface StorageInfo {
  /** Absolute path currently in use. */
  path: string;
  /** False when it fell back to the kiosk's own filesystem. */
  is_mounted: boolean;
  used: number;
  free: number;
  total: number;
  /** Mounted volumes the operator can switch to. */
  potential_paths?: string[];
  available_paths?: string[];
}

interface Options {
  isKiosk: boolean;
  nodes: Node[];
  /** Called after anything that changes what is on local storage. */
  onStorageChanged: () => void;
}

export function useKioskArchiveSync({ isKiosk, nodes, onStorageChanged }: Options) {
  const [storageInfo, setStorageInfo] = useState<StorageInfo | null>(null);
  const [availablePaths, setAvailablePaths] = useState<string[]>([]);
  const [storagePathInput, setStoragePathInput] = useState('');

  // Selection is constrained to one node at a time: a sync request names a
  // hostname, so archives from two nodes cannot go in one job.
  const [selectedNodeId, setSelectedNodeId] = useState<number | null>(null);
  const [selectedArchives, setSelectedArchives] = useState<Record<string, boolean>>({});

  const [syncing, setSyncing] = useState(false);
  const [speed, setSpeed] = useState<string | null>(null);
  const [eta, setEta] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const [taskId, setTaskId] = useState<string | null>(null);

  // The poller's handle. The interval is started from a click rather than an
  // effect, because the task id it polls does not exist until the POST
  // returns — so unmount cleanup needs a ref to reach it. Every terminal
  // branch inside clears it, but closing the tab mid-sync is not one of those
  // branches, and the callback would otherwise keep polling and setting state
  // on an unmounted component until the page reloaded.
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => () => {
    if (pollRef.current) clearInterval(pollRef.current);
  }, []);

  const refreshStorageInfo = useCallback(async () => {
    if (!isKiosk) return;
    try {
      const data = await api.get<StorageInfo>('/api/kiosk/storage');
      setStorageInfo(data);
      if (data.path) setStoragePathInput(data.path);
      if (Array.isArray(data.available_paths)) setAvailablePaths(data.available_paths);
    } catch (e) {
      console.error(e);
    }
  }, [isKiosk]);

  /** Point local storage at a different mounted volume. */
  const setStoragePath = useCallback(async (newPath: string) => {
    try {
      const data = await api.post<StorageInfo>('/api/kiosk/storage/path', { path: newPath });
      setStorageInfo(data);
      onStorageChanged();
    } catch (e: any) {
      alert(`Failed to set storage path: ${e.message || 'Unknown error'}`);
    }
  }, [onStorageChanged]);

  const archiveKey = (nodeId: number, archiveName: string) => `${nodeId}-${archiveName}`;

  const toggleArchive = useCallback((nodeId: number, archiveName: string, checked: boolean) => {
    setSelectedArchives(prev => {
      const next = { ...prev };
      const key = archiveKey(nodeId, archiveName);
      if (checked) {
        // Selecting from a different node replaces the selection rather than
        // adding to it — silently dropping the previous node's archives from a
        // job the operator thought included them would be worse.
        if (selectedNodeId !== nodeId) {
          setSelectedNodeId(nodeId);
          Object.keys(next).forEach(k => delete next[k]);
        }
        next[key] = true;
      } else {
        delete next[key];
        if (Object.keys(next).filter(k => next[k]).length === 0) {
          setSelectedNodeId(null);
        }
      }
      return next;
    });
  }, [selectedNodeId]);

  const clearSelection = useCallback(() => {
    setSelectedArchives({});
    setSelectedNodeId(null);
  }, []);

  const isSelected = useCallback(
    (nodeId: number, archiveName: string) => !!selectedArchives[archiveKey(nodeId, archiveName)],
    [selectedArchives],
  );

  /**
   * Estimate what selecting these archives will actually cost to download.
   *
   * Not the sum of their sizes. Borg deduplicates, so pulling five daily
   * snapshots of one machine transfers the first in full and only the changed
   * chunks after it. The estimate is therefore the newest archive's full
   * transfer size plus the deduplicated size of each older one.
   *
   * The newest one has to be its *whole* size, not its contribution: a kiosk
   * with an empty local repository fetches every chunk the archive references,
   * while `deduplicated_size` in an established repository counts only what was
   * new that day — often almost nothing. Reading it as the download promises a
   * 200MB transfer and delivers 40GB. `downloadSizeBytes` takes the figure borg
   * recorded, and falls back to the old 40%-of-original floor only for rows
   * predating it.
   *
   * The older archives keep `deduplicated_size`. What they really add is the
   * chunks they hold that the newest does not, which borg does not report for
   * a pair of archives; their contribution at write time is the closest thing
   * available and is the right order of magnitude.
   */
  const estimateSelection = useCallback((history: BackupHistory[]): SelectionMetrics => {
    const selected = history.filter(h => isSelected(h.node_id, h.archive_name));
    if (selected.length === 0) return { totalOriginal: 0, totalEstimatedDownload: 0 };

    const newestFirst = [...selected].sort(
      (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime(),
    );
    const totalOriginal = selected.reduce((acc, h) => acc + h.original_size, 0);

    const baseCompressed = downloadSizeBytes(newestFirst[0]);
    const additionalDelta = newestFirst.slice(1).reduce((acc, h) => acc + h.deduplicated_size, 0);

    return { totalOriginal, totalEstimatedDownload: baseCompressed + additionalDelta };
  }, [isSelected]);

  /** Start the copy, and poll the task until it finishes. */
  const copyToLocal = useCallback(async () => {
    if (selectedNodeId === null) return;
    const node = nodes.find(n => n.id === selectedNodeId);
    if (!node) return;

    const prefix = `${selectedNodeId}-`;
    const names = Object.keys(selectedArchives)
      .filter(k => selectedArchives[k] && k.startsWith(prefix))
      .map(k => k.replace(prefix, ''));
    if (names.length === 0) return;

    setSyncing(true);
    setProgress(0);
    setSpeed(null);
    setEta(null);

    const stop = () => {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = null;
      setSyncing(false);
      setTaskId(null);
    };

    try {
      const data = await api.post<{ task_id?: string }>(
        `/api/kiosk/sync/${node.hostname}?archive=${encodeURIComponent(names.join(','))}`,
      );
      if (!data.task_id) {
        setSyncing(false);
        return;
      }
      setTaskId(data.task_id);

      pollRef.current = setInterval(async () => {
        try {
          const status = await api.get<any>(`/api/tasks/${data.task_id}`);
          if (status.download_speed) setSpeed(status.download_speed);
          if (status.eta) setEta(status.eta);
          if (typeof status.progress === 'number') setProgress(status.progress);

          if (status.status === 'SUCCESS') {
            stop();
            clearSelection();
            onStorageChanged();
          } else if (status.status === 'FAILED') {
            stop();
            alert('Sync failed. Please check the logs.');
          }
        } catch {
          // The task endpoint became unreachable. Stop rather than spin: the
          // copy may well still be running, and the operator can reopen the
          // tab to see where it got to.
          stop();
        }
      }, 1000);
    } catch (e: any) {
      alert(`Failed to start copy: ${e.message || 'Unknown error'}`);
      setSyncing(false);
    }
  }, [selectedNodeId, selectedArchives, nodes, clearSelection, onStorageChanged]);

  return {
    storageInfo,
    availablePaths,
    storagePathInput,
    setStoragePathInput,
    refreshStorageInfo,
    setStoragePath,

    selectedNodeId,
    selectedArchives,
    isSelected,
    toggleArchive,
    clearSelection,
    estimateSelection,

    syncing,
    speed,
    eta,
    progress,
    taskId,
    copyToLocal,
  };
}
