import React, { useState, useEffect, useRef } from 'react';
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
  const terminalEndRef = useRef<HTMLDivElement>(null);
  const terminalContainerRef = useRef<HTMLDivElement>(null);
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
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm animate-fade-in">
      <div className="w-full max-w-3xl h-[80vh] flex flex-col bg-zinc-950 border border-zinc-800 rounded-2xl shadow-2xl overflow-hidden animate-modal-in">
        {/* Modal Header */}
        <div className="p-4 bg-zinc-900 border-b border-zinc-800 flex justify-between items-center">
          <div className="flex items-center gap-2 flex-1 min-w-0">
            <TermIcon className="text-zinc-400 flex-shrink-0" size={18} />
            <span className="font-bold text-zinc-50 text-sm truncate">{title}</span>
            {getStatusIndicator()}
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
        <div ref={terminalContainerRef} className="flex-1 p-4 overflow-y-auto font-mono text-xs text-zinc-300 bg-zinc-950 select-text space-y-1">
          {cleanLogs ? (
            cleanLogs.split('\n').map((line, idx) => {
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
