import React, { useState, useEffect, useMemo, useRef } from 'react';
import { X, Terminal as TermIcon, CheckCircle, AlertCircle, Loader, ArrowDown, ArrowUp } from 'lucide-react';
import { formatDate } from './dateUtils';
import { useTranslation } from '../context/TranslationContext';

interface TaskLogsModalProps {
  taskId: string;
  title: string;
  timezone?: string;
  onClose: () => void;
  bandwidth?: { rx_speed: number; tx_speed: number } | null;
}

const formatSpeed = (bytesPerSec: number): string => {
  if (bytesPerSec === 0) return '0 B/s';
  const k = 1024;
  const sizes = ['B/s', 'KB/s', 'MB/s', 'GB/s'];
  const i = Math.floor(Math.log(bytesPerSec) / Math.log(k));
  return parseFloat((bytesPerSec / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
};

export default function TaskLogsModal({ taskId, title, timezone, onClose, bandwidth }: TaskLogsModalProps) {
  const { t } = useTranslation();
  const [status, setStatus] = useState('PENDING');
  const [logs, setLogs] = useState('');
  const [downloadSpeed, setDownloadSpeed] = useState('');
  const [eta, setEta] = useState('');
  const terminalEndRef = useRef<HTMLDivElement>(null);
  const terminalContainerRef = useRef<HTMLDivElement>(null);
  const notFoundCountRef = useRef(0);
  //: How many characters of this task's log we already hold, so each poll can
  //: ask for the remainder instead of the whole thing.
  const receivedRef = useRef(0);
  const errorCountRef = useRef(0);

  const fetchLogs = async () => {
    try {
      // Ask only for the part we do not already have. Re-fetching the whole
      // log once a second is quadratic in its length, and a long provision
      // produces megabytes of it.
      const res = await fetch(`/api/tasks/${taskId}?since=${receivedRef.current}`);
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
          return;
        }
        // Anything other than a 404 used to fall straight through, leaving the
        // poll running at 1 Hz forever against an endpoint that was refusing
        // it. Give up after a few consecutive failures and say so.
        errorCountRef.current += 1;
        if (errorCountRef.current >= 5) {
          setStatus('FAILED');
          setLogs((prev) => prev + `\n[SYSTEM] Lost contact with the server (HTTP ${res.status}). Stopped following this task.`);
        }
        return;
      }
      notFoundCountRef.current = 0;
      errorCountRef.current = 0;

      const data = await res.json();
      setStatus(data.status);

      // This modal is served by two different backends. The orchestrator's
      // TaskLogResponse carries only log_output; the kiosk payload client also
      // returns `logs`, `download_speed` and `eta` for restore transfers. The
      // fallbacks are for the kiosk, not dead code — the speed/ETA badge below
      // simply never appears on the orchestrator, which has nothing to put in it.
      const chunk: string = data.log_output ?? data.logs ?? '';
      if (typeof data.log_length === 'number') {
        // Incremental protocol. log_offset is 0 when the server sent the whole
        // log — either the first poll or a log that was reset behind us.
        if (data.log_offset > 0) {
          if (chunk) setLogs((prev) => prev + chunk);
        } else {
          setLogs(chunk);
        }
        receivedRef.current = data.log_length;
      } else {
        // Kiosk payload client: no incremental support, always the full log.
        setLogs(chunk);
      }

      setDownloadSpeed(data.download_speed || '');
      setEta(data.eta || '');
    } catch (e) {
      console.error(e);
      errorCountRef.current += 1;
      if (errorCountRef.current >= 5) {
        setStatus('FAILED');
      }
    }
  };

  // A new task means a new log; forget how much of the previous one we had.
  useEffect(() => {
    receivedRef.current = 0;
    errorCountRef.current = 0;
    notFoundCountRef.current = 0;
    setLogs('');
  }, [taskId]);

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
    if (terminalContainerRef.current) {
      terminalContainerRef.current.scrollTo({
        top: terminalContainerRef.current.scrollHeight,
        behavior: 'smooth'
      });
    }
  }, [logs]);

  const getStatusIndicator = () => {
    switch (status) {
      case 'SUCCESS':
        return <span className="inline-flex items-center gap-1 text-emerald-400 text-xs font-bold bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 rounded-full"><CheckCircle size={12} /> {t('success')}</span>;
      case 'FAILED':
        return <span className="inline-flex items-center gap-1 text-rose-400 text-xs font-bold bg-rose-500/10 border border-rose-500/20 px-2 py-0.5 rounded-full"><AlertCircle size={12} /> {t('failed')}</span>;
      case 'RUNNING':
        return <span className="inline-flex items-center gap-1 text-sky-400 text-xs font-bold bg-sky-500/10 border border-sky-500/20 px-2 py-0.5 rounded-full"><Loader size={12} className="animate-spin" /> {t('running')}</span>;
      case 'PENDING':
      default:
        return <span className="inline-flex items-center gap-1 text-zinc-400 text-xs font-bold bg-zinc-500/10 border border-zinc-500/20 px-2 py-0.5 rounded-full"><Loader size={12} className="animate-spin" /> {t('pending')}</span>;
    }
  };

  // The log is split once per change, not three times per render. This
  // component re-renders every second while a task runs, and a long provision
  // produces tens of thousands of lines — splitting the whole buffer twice to
  // compute a progress bar and again to draw it was the bulk of the work.
  const { logLines, progressInfo } = useMemo(() => {
    const lines = logs.split('\n');
    const kept: string[] = [];
    let progress: { percent: number; step: string } | null = null;

    for (const line of lines) {
      if (line.includes('[PROGRESS]')) {
        // Last one wins: progress is cumulative, not a list.
        const match = line.match(/\[PROGRESS\]\s*(\d+):(.*)/);
        if (match) {
          // The step is kept unresolved so this memo stays keyed on `logs`
          // alone — `t` is a fresh closure every render, and depending on it
          // would re-split the whole buffer each second.
          progress = {
            percent: Math.min(100, Math.max(0, parseInt(match[1], 10))),
            step: match[2].trim(),
          };
        }
        continue;
      }
      kept.push(line);
    }
    return { logLines: kept, progressInfo: progress };
  }, [logs]);

  const cleanLogs = logLines.length ? logLines.join('\n') : '';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm animate-fade-in">
      <div className="w-full max-w-3xl h-[80%] max-h-[85%] flex flex-col bg-zinc-950 border border-zinc-800 rounded-2xl shadow-2xl overflow-hidden animate-modal-in">
        {/* Modal Header */}
        <div className="p-4 bg-zinc-900 border-b border-zinc-800 flex justify-between items-center">
          <div className="flex items-center gap-2 flex-1 min-w-0">
            <TermIcon className="text-zinc-400 flex-shrink-0" size={18} />
            <span className="font-bold text-zinc-50 text-sm truncate">{title}</span>
            {getStatusIndicator()}
            {downloadSpeed && (
              <span className="inline-flex items-center gap-1 text-indigo-400 text-[10px] font-bold bg-indigo-500/10 border border-indigo-500/20 px-2 py-0.5 rounded-full select-none shrink-0">
                <ArrowDown size={10} className="animate-pulse" />
                {downloadSpeed}
                {eta && eta !== '--' && ` (ETA: ${eta})`}
              </span>
            )}
          </div>

          {/* Bandwidth Widget */}
          {bandwidth && (
            <div className="flex-shrink-0 flex items-center gap-3 bg-zinc-950/40 border border-zinc-800/60 rounded-xl px-3 py-1 shadow-inner mr-4">
              <div className="flex items-center gap-1.5" title={t('bandwidthDownload') || 'Download'}>
                <ArrowDown size={11} className={bandwidth.rx_speed > 1024 ? 'text-emerald-400 animate-pulse' : 'text-zinc-600'} />
                <span className="text-[9px] text-zinc-500 font-bold font-mono">RX</span>
                <span className={`text-[10px] font-mono font-semibold transition-colors duration-500 ${bandwidth.rx_speed > 1024 ? 'text-zinc-200' : 'text-zinc-500'}`}>
                  {formatSpeed(bandwidth.rx_speed)}
                </span>
              </div>
              <div className="w-px h-2.5 bg-zinc-800" />
              <div className="flex items-center gap-1.5" title={t('bandwidthUpload') || 'Upload'}>
                <ArrowUp size={11} className={bandwidth.tx_speed > 1024 ? 'text-indigo-400 animate-pulse' : 'text-zinc-600'} />
                <span className="text-[9px] text-zinc-500 font-bold font-mono">TX</span>
                <span className={`text-[10px] font-mono font-semibold transition-colors duration-500 ${bandwidth.tx_speed > 1024 ? 'text-zinc-200' : 'text-zinc-500'}`}>
                  {formatSpeed(bandwidth.tx_speed)}
                </span>
              </div>
            </div>
          )}

          <button
            onClick={onClose}
            className="p-1 text-zinc-400 hover:text-zinc-50 hover:bg-zinc-800 rounded transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        {/* Progress Bar */}
        {progressInfo && (status === 'RUNNING' || status === 'SUCCESS') && (
          <div className="bg-zinc-900 px-6 py-3 border-b border-zinc-800/80 space-y-1.5">
            <div className="flex justify-between items-center text-xs font-semibold">
              {/* The orchestrator writes a translation key here
                  ("bootstrap_installing_deps"); the kiosk writes a finished
                  English sentence. t() returns its argument unchanged when
                  there is no such key, so both render, and logs written
                  before this protocol existed still read as they were. */}
              <span className="text-zinc-300">{t(progressInfo.step)}</span>
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
        <div ref={terminalContainerRef} className="flex-1 p-4 overflow-y-auto font-mono text-xs text-zinc-300 bg-zinc-950 select-text space-y-1">
          {cleanLogs ? (
            logLines.map((line, idx) => {
              const match = line.match(/^\[(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})\](.*)/);
              if (match) {
                const utcDateStr = `${match[1]}T${match[2]}Z`;
                const localTimeStr = formatDate(utcDateStr, timezone);
                return (
                  <div key={idx} className="whitespace-pre-wrap leading-relaxed">
                    <span className="text-zinc-500 mr-1">[{localTimeStr}]</span>
                    {match[3]}
                  </div>
                );
              }
              return (
                <div key={idx} className="whitespace-pre-wrap leading-relaxed">
                  {line}
                </div>
              );
            })
          ) : (
            <div className="text-zinc-500 italic">{t('noOutputLogs')}</div>
          )}

          {status === 'SUCCESS' && (
            <div className="text-emerald-600 dark:text-emerald-400 font-bold mt-2 border-t border-emerald-500/20 pt-2 flex items-center gap-1.5">
              <CheckCircle size={14} /> {t('taskSuccessMessage')}
            </div>
          )}
          {status === 'FAILED' && (
            <div className="text-rose-600 dark:text-rose-400 font-bold mt-2 border-t border-rose-500/20 pt-2 flex items-center gap-1.5">
              <AlertCircle size={14} /> {t('taskFailedMessage')}
            </div>
          )}

          <div ref={terminalEndRef} />
        </div>

        {/* Action footer when execution completes */}
        {(status === 'SUCCESS' || status === 'FAILED') && (
          <div className="p-4 bg-zinc-900 border-t border-zinc-800 flex justify-between items-center px-6">
            <span className="text-xs text-zinc-400 font-medium">
              {status === 'SUCCESS' 
                ? t('allOperationsCompleted') 
                : t('executionFailedReview')}
            </span>
            <button
              onClick={onClose}
              className={`px-4 py-2 text-xs font-semibold rounded-lg transition-colors ${
                status === 'SUCCESS' ? 'bg-emerald-600 hover:bg-emerald-500 text-white' : 'bg-zinc-800 hover:bg-zinc-700 text-zinc-100'
              }`}
            >
              {t('closeConsole')}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
