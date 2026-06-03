import React, { useState, useEffect, useRef } from 'react';
import { X, Terminal as TermIcon, CheckCircle, AlertCircle, Loader } from 'lucide-react';

interface TaskLogsModalProps {
  taskId: string;
  title: string;
  onClose: () => void;
}

export default function TaskLogsModal({ taskId, title, onClose }: TaskLogsModalProps) {
  const [status, setStatus] = useState('PENDING');
  const [logs, setLogs] = useState('');
  const terminalEndRef = useRef<HTMLDivElement>(null);
  const notFoundCountRef = useRef(0);

  const fetchLogs = async () => {
    try {
      const res = await fetch(`/api/tasks/${taskId}`);
      if (!res.ok) {
        if (res.status === 404) {
          notFoundCountRef.current += 1;
          // After 15 consecutive 404s (~15s), the task likely crashed before
          // creating its TaskLog record (e.g. import error in worker).
          if (notFoundCountRef.current >= 15) {
            setStatus('FAILED');
            setLogs(
              '[SYSTEM] Task failed to start. The worker process crashed before producing any log output.\n' +
              'This usually indicates a code-level error in the worker container (e.g., missing module).\n' +
              'Check `docker compose logs worker` on the server for details.'
            );
          }
        }
        return;
      }
      // Reset counter on successful fetch
      notFoundCountRef.current = 0;
      const data = await res.json();
      setStatus(data.status);
      setLogs(data.log_output);
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => {
    fetchLogs();
    const interval = setInterval(() => {
      if (status === 'PENDING' || status === 'RUNNING') {
        fetchLogs();
      } else {
        clearInterval(interval);
      }
    }, 1000);

    return () => clearInterval(interval);
  }, [taskId, status]);

  // Autoscroll
  useEffect(() => {
    terminalEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  const getStatusIndicator = () => {
    switch (status) {
      case 'SUCCESS':
        return <span className="inline-flex items-center gap-1 text-emerald-400 text-xs font-bold bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 rounded-full"><CheckCircle size={12} /> Success</span>;
      case 'FAILED':
        return <span className="inline-flex items-center gap-1 text-rose-400 text-xs font-bold bg-rose-500/10 border border-rose-500/20 px-2 py-0.5 rounded-full"><AlertCircle size={12} /> Failed</span>;
      case 'RUNNING':
        return <span className="inline-flex items-center gap-1 text-sky-400 text-xs font-bold bg-sky-500/10 border border-sky-500/20 px-2 py-0.5 rounded-full"><Loader size={12} className="animate-spin" /> Running</span>;
      case 'PENDING':
      default:
        return <span className="inline-flex items-center gap-1 text-zinc-400 text-xs font-bold bg-zinc-500/10 border border-zinc-500/20 px-2 py-0.5 rounded-full"><Loader size={12} className="animate-spin" /> Pending</span>;
    }
  };

  // Parse percentage and description
  const getProgressInfo = () => {
    const progressLines = logs.split('\n').filter(line => line.includes('[PROGRESS]'));
    if (progressLines.length === 0) return null;
    const lastLine = progressLines[progressLines.length - 1];
    const match = lastLine.match(/\[PROGRESS\]\s*(\d+):(.*)/);
    if (match) {
      return {
        percent: Math.min(100, Math.max(0, parseInt(match[1], 10))),
        description: match[2].trim()
      };
    }
    return null;
  };

  // Filter out [PROGRESS] lines for a clean terminal look
  const cleanLogs = logs.split('\n')
    .filter(line => !line.includes('[PROGRESS]'))
    .join('\n');

  const progressInfo = getProgressInfo();

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
      <div className="w-full max-w-3xl h-[80vh] flex flex-col bg-zinc-950 border border-zinc-800 rounded-2xl shadow-2xl overflow-hidden">
        {/* Modal Header */}
        <div className="p-4 bg-zinc-900 border-b border-zinc-800 flex justify-between items-center">
          <div className="flex items-center gap-2">
            <TermIcon className="text-zinc-400" size={18} />
            <span className="font-bold text-white text-sm">{title}</span>
            {getStatusIndicator()}
          </div>
          <button
            onClick={onClose}
            className="p-1 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        {/* Progress Bar */}
        {progressInfo && (status === 'RUNNING' || status === 'SUCCESS') && (
          <div className="bg-zinc-900 px-6 py-3 border-b border-zinc-800/80 space-y-1.5">
            <div className="flex justify-between items-center text-xs font-semibold">
              <span className="text-zinc-300">{progressInfo.description}</span>
              <span className="text-sky-400 font-bold">{progressInfo.percent}%</span>
            </div>
            <div className="w-full h-2 bg-zinc-850 rounded-full overflow-hidden border border-zinc-800">
              <div 
                className="h-full bg-gradient-to-r from-sky-400 to-indigo-500 rounded-full transition-all duration-500 ease-out"
                style={{ width: `${progressInfo.percent}%` }}
              />
            </div>
          </div>
        )}

        {/* Console logs */}
        <div className="flex-1 p-4 overflow-y-auto font-mono text-xs text-zinc-300 bg-black/95 select-text space-y-1">
          {cleanLogs ? (
            cleanLogs.split('\n').map((line, idx) => (
              <div key={idx} className="whitespace-pre-wrap leading-relaxed">
                {line}
              </div>
            ))
          ) : (
            <div className="text-zinc-600 italic">No output logs generated yet...</div>
          )}

          {status === 'SUCCESS' && (
            <div className="text-emerald-400 font-bold mt-2 border-t border-emerald-500/20 pt-2 flex items-center gap-1.5">
              <CheckCircle size={14} /> [SYSTEM] Task execution finished successfully. You can close this console.
            </div>
          )}
          {status === 'FAILED' && (
            <div className="text-rose-400 font-bold mt-2 border-t border-rose-500/20 pt-2 flex items-center gap-1.5">
              <AlertCircle size={14} /> [SYSTEM] Task execution failed. Check details above.
            </div>
          )}

          <div ref={terminalEndRef} />
        </div>

        {/* Action footer when execution completes */}
        {(status === 'SUCCESS' || status === 'FAILED') && (
          <div className="p-4 bg-zinc-900 border-t border-zinc-800 flex justify-between items-center px-6">
            <span className="text-xs text-zinc-400 font-medium">
              {status === 'SUCCESS' 
                ? 'All operations completed successfully.' 
                : 'Execution failed. Review errors in the console log.'}
            </span>
            <button
              onClick={onClose}
              className={`px-4 py-2 text-xs font-semibold text-white rounded-lg transition-colors ${
                status === 'SUCCESS' ? 'bg-emerald-600 hover:bg-emerald-500' : 'bg-zinc-800 hover:bg-zinc-700'
              }`}
            >
              Close Console
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
