import React from 'react';
import { HardDrive, RefreshCw } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';

interface Device {
  name: string;
  size: number;
  model: string;
  rotational: boolean;
  disk_type: string;
  is_usb?: boolean;
}

interface StorageInfo {
  total: number;
  used: number;
  free: number;
  path: string;
  is_mounted: boolean;
}

interface DeviceScannerSectionProps {
  devices: Device[];
  scanning: boolean;
  onRefreshDevices: () => void;
  isKiosk: boolean;
  storageInfo: StorageInfo | null;
  getFormatSize: (bytes: number) => string;
}

export function DeviceScannerSection({
  devices,
  scanning,
  onRefreshDevices,
  isKiosk,
  storageInfo,
  getFormatSize,
}: DeviceScannerSectionProps) {
  const { t } = useTranslation();

  return (
    <div className="space-y-6">
      <div className="p-6 bg-zinc-900 border border-zinc-800 rounded-2xl space-y-4">
        <div className="flex justify-between items-center">
          <h3 className="text-sm font-bold text-zinc-50 flex items-center gap-2">
            <HardDrive size={16} /> {t('deviceScanner')}
          </h3>
          <button
            onClick={onRefreshDevices}
            disabled={scanning}
            className="p-1 hover:bg-zinc-800 text-zinc-400 hover:text-white rounded transition-colors"
          >
            <RefreshCw size={14} className={scanning ? 'animate-spin' : ''} />
          </button>
        </div>
        <div className="space-y-2 max-h-80 overflow-y-auto">
          {devices.length === 0 ? (
            <div className="text-center py-8 text-xs text-zinc-500">{t('noDrivesFound')}</div>
          ) : (
            devices.map((d) => (
              <div key={d.name} className="p-3 bg-zinc-950 border border-zinc-800/80 rounded-xl space-y-1">
                <div className="flex justify-between text-xs font-bold text-zinc-50">
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

      {isKiosk && storageInfo && (
        <div className="p-6 bg-zinc-900 border border-zinc-800 rounded-2xl space-y-4 animate-fade-in">
          <div className="flex justify-between items-center">
            <h3 className="text-sm font-bold text-zinc-50 flex items-center gap-2">
              <HardDrive size={16} className="text-indigo-400" />
              {t('localBackupStorage')}
            </h3>
            <span
              className={`text-[10px] px-2 py-0.5 rounded-full font-mono font-bold ${
                storageInfo.is_mounted
                  ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                  : 'bg-amber-500/10 text-amber-400 border border-amber-500/20'
              }`}
            >
              {storageInfo.is_mounted ? t('usbMountedBadge') : t('fallbackBadge')}
            </span>
          </div>

          <div className="space-y-3">
            <div className="flex justify-between text-xs text-zinc-400">
              <span>Mount Path</span>
              <span className="font-mono text-zinc-300 text-right max-w-[150px] truncate" title={storageInfo.path}>
                {storageInfo.path}
              </span>
            </div>

            <div className="space-y-1.5">
              <div className="flex justify-between text-xs font-semibold">
                <span className="text-zinc-400">{t('usedSpace', { size: getFormatSize(storageInfo.used) })}</span>
                <span className="text-zinc-100">
                  {((storageInfo.used / storageInfo.total) * 100).toFixed(0)}%
                </span>
              </div>

              <div className="w-full bg-zinc-950 h-2 rounded-full overflow-hidden border border-zinc-800/80 p-[1px]">
                <div
                  className={`h-full rounded-full transition-all duration-500 ${
                    storageInfo.free / storageInfo.total < 0.1
                      ? 'bg-rose-500'
                      : storageInfo.free / storageInfo.total < 0.25
                      ? 'bg-amber-500'
                      : 'bg-indigo-500'
                  }`}
                  style={{ width: `${(storageInfo.used / storageInfo.total) * 100}%` }}
                />
              </div>
            </div>

            <div className="flex justify-between text-xs font-semibold">
              <span className="text-zinc-400">{t('freeSpace')}</span>
              <span className="text-emerald-400">{getFormatSize(storageInfo.free)}</span>
            </div>

            <div className="flex justify-between text-xs">
              <span className="text-zinc-400">{t('totalCapacity')}</span>
              <span className="text-zinc-300 font-semibold">{getFormatSize(storageInfo.total)}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
