import React from 'react';
import { History } from 'lucide-react';

interface BackupHistory {
  id: number;
  archive_name: string;
  timestamp: string;
  original_size: number;
  deduplicated_size: number;
  status: string;
  comment: string | null;
}

interface NodeBackupHistoryProps {
  history: BackupHistory[];
  language: string;
  formatBytes: (bytes: number) => string;
  t: (key: string) => string;
}

export default function NodeBackupHistory({
  history,
  language,
  formatBytes,
  t,
}: NodeBackupHistoryProps) {
  return (
    <div className="bg-zinc-950/30 border border-zinc-800/80 rounded-xl p-5 space-y-4">
      <h4 className="font-bold text-zinc-200 text-sm border-b border-zinc-800 pb-2 flex items-center gap-1.5">
        <History className="h-4.5 w-4.5 text-indigo-400" />
        Backup History & Archives
      </h4>

      <div className="overflow-x-auto rounded-lg border border-zinc-800">
        <table className="w-full text-left border-collapse text-sm">
          <thead>
            <tr className="bg-zinc-950 text-zinc-400 font-semibold border-b border-zinc-800">
              <th className="p-3">Archive Name</th>
              <th className="p-3">Date & Time (UTC)</th>
              <th className="p-3">Original Size</th>
              <th className="p-3">Deduplicated Size</th>
              <th className="p-3">Status</th>
              <th className="p-3">Comment</th>
            </tr>
          </thead>
          <tbody>
            {history.map((row) => (
              <tr key={row.id} className="border-b border-zinc-800/80 hover:bg-zinc-850/30 text-zinc-200">
                <td className="p-3 font-mono text-xs">{row.archive_name}</td>
                <td className="p-3 font-mono text-xs">
                  {new Date(row.timestamp).toLocaleString(
                    language === 'ru' ? 'ru-RU' : language === 'uk' ? 'uk-UA' : 'en-US'
                  )}
                </td>
                <td className="p-3">{formatBytes(row.original_size)}</td>
                <td className="p-3">{formatBytes(row.deduplicated_size)}</td>
                <td className="p-3">
                  <span
                    className={`px-2 py-0.5 rounded text-xs font-semibold ${
                      row.status === 'SUCCESS'
                        ? 'bg-emerald-500/10 text-emerald-400'
                        : 'bg-rose-500/10 text-rose-400'
                    }`}
                  >
                    {row.status}
                  </span>
                </td>
                <td className="p-3 max-w-[200px] truncate text-zinc-400" title={row.comment || ''}>
                  {row.comment || '-'}
                </td>
              </tr>
            ))}
            {history.length === 0 && (
              <tr>
                <td colSpan={6} className="p-6 text-center text-zinc-500">
                  No backup snapshots executed yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
