import React from 'react';
import { HardDrive } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';
import { formatBytes } from './formatBytes';
import type { StorageInfo } from '../hooks/useKioskArchiveSync';

/**
 * Where the kiosk is putting the archives it downloads, and how full it is.
 *
 * Shown only in local mode, where it is the operator's one warning that the
 * stick is nearly full — a sync that fills the volume fails partway and leaves
 * an archive that cannot be restored from.
 *
 * `is_mounted` false means no USB volume was found and the kiosk fell back to
 * its own filesystem. That is labelled rather than hidden: on a live image
 * that filesystem is RAM, and a few tens of gigabytes of archives will take
 * the machine down.
 */
export interface KioskStoragePanelProps {
  storage: StorageInfo;
  onSelectPath: (path: string) => void | Promise<void>;
}

export default function KioskStoragePanel({ storage, onSelectPath }: KioskStoragePanelProps) {
  const { t } = useTranslation();

  return (
        <div className="p-4 bg-zinc-900 border border-zinc-800 rounded-2xl flex flex-col md:flex-row items-stretch md:items-center justify-between gap-6 mb-6">
          <div className="flex-1 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <div className="p-2.5 bg-indigo-500/10 text-indigo-400 rounded-xl border border-indigo-500/20">
                <HardDrive size={20} />
              </div>
              <div>
                <h4 className="text-xs font-black text-zinc-200 uppercase tracking-wider">{t('localBackupStorage')}</h4>
                <div className="flex items-center gap-2 mt-1">
                  <span className={`text-[9px] px-1.5 py-0.5 rounded font-mono font-bold ${
                    storage.is_mounted
                      ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                      : 'bg-amber-500/10 text-amber-400 border border-amber-500/20'
                  }`}>
                    {storage.is_mounted ? t('usbMountedBadge') : t('fallbackBadge')}
                  </span>
                  <span className="text-[10px] text-zinc-500 font-mono truncate max-w-[200px]" title={storage.path}>
                    {storage.path}
                  </span>
                </div>
              </div>
            </div>

            <div className="flex-1 max-w-xs space-y-1">
              <div className="flex justify-between text-[10px] font-semibold text-zinc-400">
                <span>{t('usedSpace', { size: formatBytes(storage.used) })}</span>
                <span>{((storage.used / storage.total) * 100).toFixed(0)}%</span>
              </div>
              <div className="w-full bg-zinc-950 h-1.5 rounded-full overflow-hidden border border-zinc-850 p-[1px]">
                <div
                  className={`h-full rounded-full transition-all duration-500 ${
                    storage.free / storage.total < 0.1
                      ? 'bg-rose-500'
                      : storage.free / storage.total < 0.25
                      ? 'bg-amber-500'
                      : 'bg-indigo-500'
                  }`}
                  style={{ width: `${(storage.used / storage.total) * 100}%` }}
                />
              </div>
              <div className="flex justify-between text-[10px] font-medium text-zinc-500">
                <span>{t('freeSpace')}: <span className="text-emerald-400">{formatBytes(storage.free)}</span></span>
                <span>{t('totalCapacity')}: {formatBytes(storage.total)}</span>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-3 border-t md:border-t-0 md:border-l border-zinc-800 pt-4 md:pt-0 md:pl-6">
            <div className="flex flex-col">
              <span className="text-[10px] font-bold text-zinc-400 uppercase tracking-wider mb-1">{t('mountPath') || 'Mount Path'}</span>
              <select
                value={storage.path}
                onChange={async (e) => {
                  const val = e.target.value;
                  if (val === '__custom__') {
                    const custom = prompt(t('enterCustomStoragePath') || "Enter custom absolute storage path:", storage.path);
                    if (custom && custom.trim().startsWith("/")) {
                      await onSelectPath(custom.trim());
                    }
                  } else {
                    await onSelectPath(val);
                  }
                }}
                className="bg-zinc-950 text-zinc-300 border border-zinc-800 rounded-lg px-2.5 py-1.5 text-xs focus:ring-0 w-44 truncate cursor-pointer hover:border-zinc-700 transition-colors font-mono"
              >
                {(storage.potential_paths || [storage.path]).map((p: string) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
                <option value="__custom__">⚙️ Custom Path...</option>
              </select>
            </div>
          </div>
        </div>
  );
}
