import React from 'react';

interface TaskLog {
  id: string;
  task_type: string;
  status: string;
  created_at: string;
  log_output: string;
}

interface NodeConsoleLogsProps {
  taskLogs: TaskLog[];
  selectedLogId: string;
  setSelectedLogId: (id: string) => void;
  language: string;
  t: (key: string) => string;
}

export default function NodeConsoleLogs({
  taskLogs,
  selectedLogId,
  setSelectedLogId,
  language,
  t,
}: NodeConsoleLogsProps) {
  const selectedLog = taskLogs.find((x) => x.id === selectedLogId);

  const handleCopyLog = () => {
    if (selectedLog) {
      navigator.clipboard.writeText(selectedLog.log_output);
      alert(t('logCopiedAlert'));
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <label className="text-xs font-semibold text-zinc-400">{t('selectSession')}</label>
          <select
            value={selectedLogId}
            onChange={(e) => setSelectedLogId(e.target.value)}
            className="px-3 py-1.5 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-xs focus:outline-none focus:border-indigo-500"
          >
            {taskLogs.map((tl) => (
              <option key={tl.id} value={tl.id}>
                {tl.task_type} — {new Date(tl.created_at).toLocaleString(language === 'ru' ? 'ru-RU' : language === 'uk' ? 'uk-UA' : 'en-US')} ({tl.status})
              </option>
            ))}
            {taskLogs.length === 0 && <option value="">{t('noLogSessions')}</option>}
          </select>
        </div>
        {selectedLogId && (
          <button
            onClick={handleCopyLog}
            className="px-3 py-1.5 bg-zinc-850 hover:bg-zinc-800 text-zinc-200 border border-zinc-700/80 rounded-lg text-xs font-semibold transition cursor-pointer"
          >
            {t('copyLog')}
          </button>
        )}
      </div>

      <div className="bg-zinc-950 border border-zinc-850 rounded-xl p-4 font-mono text-xs overflow-hidden">
        <pre className="text-emerald-400 bg-black p-4 rounded-lg overflow-y-auto max-h-[350px] whitespace-pre-wrap leading-relaxed">
          {selectedLog?.log_output || t('selectSessionPlaceholder')}
        </pre>
      </div>
    </div>
  );
}
