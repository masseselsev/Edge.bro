import React, { useState, useEffect } from 'react';
import { History, ChevronLeft, ChevronRight, FileText } from 'lucide-react';
import ArchiveFilesModal from './ArchiveFilesModal';

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
  const [currentPage, setCurrentPage] = useState(1);
  const [itemsPerPage, setItemsPerPage] = useState(5);
  const [selectedArchive, setSelectedArchive] = useState<{ id: number; name: string } | null>(null);

  useEffect(() => {
    setCurrentPage(1);
  }, [history.length]);

  const totalPages = Math.ceil(history.length / itemsPerPage);
  const startIndex = (currentPage - 1) * itemsPerPage;
  const slicedHistory = history.slice(startIndex, startIndex + itemsPerPage);

  return (
    <div className="bg-zinc-950/30 border border-zinc-800/80 rounded-xl p-5 space-y-4">
      <h4 className="font-bold text-zinc-200 text-sm border-b border-zinc-800 pb-2 flex items-center gap-1.5">
        <History className="h-4.5 w-4.5 text-indigo-400" />
        {t('backupHistoryArchives') || 'Backup History & Archives'}
      </h4>

      <div className="overflow-x-auto rounded-lg border border-zinc-800">
        <table className="w-full text-left border-collapse text-sm">
          <thead>
            <tr className="bg-zinc-950 text-zinc-400 font-semibold border-b border-zinc-800">
              <th className="p-3">{t('snapshotColumn') || 'Archive Name'}</th>
              <th className="p-3">{t('timestampColumn') || 'Date & Time (UTC)'}</th>
              <th className="p-3">{t('originalSizeColumn') || 'Original Size'}</th>
              <th className="p-3">{t('dedupSizeColumn') || 'Deduplicated Size'}</th>
              <th className="p-3">{t('statusColumn') || 'Status'}</th>
              <th className="p-3">{t('commentColumn') || 'Comment'}</th>
              <th className="p-3 text-right">{t('actions') || 'Actions'}</th>
            </tr>
          </thead>
          <tbody>
            {slicedHistory.map((row) => (
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
                <td className="p-3 text-right">
                  {row.status === 'SUCCESS' && (
                    <button
                      onClick={() => setSelectedArchive({ id: row.id, name: row.archive_name })}
                      className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded border border-indigo-500/20 text-indigo-400 hover:bg-indigo-500/10 transition"
                      title={t('viewArchiveFiles') || 'View Files'}
                    >
                      <FileText className="h-3.5 w-3.5" />
                      <span>{t('viewArchiveFiles') || 'Files'}</span>
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {history.length === 0 && (
              <tr>
                <td colSpan={7} className="p-6 text-center text-zinc-500">
                  {t('noBackupSnapshots') || 'No backup snapshots executed yet.'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {history.length > 0 && (
        <div className="flex flex-col sm:flex-row items-center justify-between gap-3 pt-2 text-xs text-zinc-400">
          <div className="flex items-center gap-2">
            <span>Show:</span>
            <select
              value={itemsPerPage}
              onChange={(e) => {
                setItemsPerPage(Number(e.target.value));
                setCurrentPage(1);
              }}
              className="bg-zinc-900 border border-zinc-800 rounded px-2 py-1 text-zinc-200 focus:outline-none focus:border-indigo-500 cursor-pointer"
            >
              <option value={5}>5 items</option>
              <option value={10}>10 items</option>
              <option value={20}>20 items</option>
              <option value={50}>50 items</option>
            </select>
            <span>of {history.length} snapshots</span>
          </div>

          <div className="flex items-center gap-1.5">
            <button
              onClick={() => setCurrentPage((prev) => Math.max(1, prev - 1))}
              disabled={currentPage === 1}
              className="p-1.5 bg-zinc-900 hover:bg-zinc-800 disabled:opacity-40 disabled:cursor-not-allowed border border-zinc-800 rounded text-zinc-300 transition"
              title="Previous Page"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <span className="px-2">
              Page {currentPage} of {totalPages || 1}
            </span>
            <button
              onClick={() => setCurrentPage((prev) => Math.min(totalPages, prev + 1))}
              disabled={currentPage === totalPages || totalPages === 0}
              className="p-1.5 bg-zinc-900 hover:bg-zinc-800 disabled:opacity-40 disabled:cursor-not-allowed border border-zinc-800 rounded text-zinc-300 transition"
              title="Next Page"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}

      {selectedArchive !== null && (
        <ArchiveFilesModal
          historyId={selectedArchive.id}
          archiveName={selectedArchive.name}
          onClose={() => setSelectedArchive(null)}
        />
      )}
    </div>
  );
}

