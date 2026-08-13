import React, { useState, useEffect, useRef } from 'react';
import { Terminal, Search, CheckCircle2, AlertCircle, RefreshCw, Eye, ShieldAlert, ChevronLeft, ChevronRight } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';

interface SystemLog {
  id: number;
  level: string;
  message: string;
  created_at: string;
}

import { formatDate } from './dateUtils';
import type { TaskLog } from '../types';

interface LogsTabProps {
  onViewLogs: (taskId: string, title: string) => void;
  timezone?: string;
  isKiosk?: boolean;
}

export default function LogsTab({ onViewLogs, timezone, isKiosk = false }: LogsTabProps) {
  const { t } = useTranslation();
  const [tasks, setTasks] = useState<TaskLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [page, setPage] = useState(1);
  const [limit, setLimit] = useState(20);
  const [totalTasks, setTotalTasks] = useState(0);
  const [totalPages, setTotalPages] = useState(1);

  // Debug Mode State
  const [debugMode, setDebugMode] = useState(false);
  const [debugLogs, setDebugLogs] = useState<SystemLog[]>([]);
  const [loadingDebug, setLoadingDebug] = useState(false);
  const terminalEndRef = useRef<HTMLDivElement>(null);
  const terminalContainerRef = useRef<HTMLDivElement>(null);
  const isInitialLoad = useRef(true);

  useEffect(() => {
    setPage(1);
  }, [searchQuery]);

  const fetchTasks = async () => {
    try {
      const query = new URLSearchParams({
        page: String(page),
        limit: String(limit),
      });
      if (searchQuery.trim()) {
        query.append('search', searchQuery.trim());
      }
      const res = await fetch(`/api/tasks?${query.toString()}`);
      if (res.ok) {
        const data = await res.json();
        setTasks(data.items || []);
        setTotalTasks(data.total || 0);
        setTotalPages(data.pages || 1);
      }
    } catch (e) {
      console.error('Failed to fetch tasks:', e);
    } finally {
      setLoading(false);
    }
  };

  const fetchDebugLogs = async () => {
    try {
      const res = await fetch('/api/tasks/debug-logs');
      if (res.ok) {
        const data = await res.json();
        // Since we retrieve ordered desc (most recent first), we reverse it for terminal timeline
        const chronLogs = [...data].reverse();
        setDebugLogs(chronLogs);
      }
    } catch (e) {
      console.error('Failed to fetch debug logs:', e);
    } finally {
      setLoadingDebug(false);
    }
  };

  useEffect(() => {
    if (!debugMode) {
      fetchTasks();
      const interval = setInterval(fetchTasks, 5000);
      return () => clearInterval(interval);
    } else {
      setLoadingDebug(true);
      fetchDebugLogs();
      const interval = setInterval(fetchDebugLogs, 3000);
      return () => clearInterval(interval);
    }
  }, [debugMode, page, limit, searchQuery]);

  // Reset initial load flag when entering debug mode
  useEffect(() => {
    if (debugMode) {
      isInitialLoad.current = true;
    }
  }, [debugMode]);

  // Scroll to bottom when new logs arrive, but only if user is already near the bottom or on initial load
  useEffect(() => {
    if (debugMode && terminalContainerRef.current) {
      const container = terminalContainerRef.current;
      const threshold = 100; // pixels from bottom
      const isNearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < threshold;
      
      if (isNearBottom || isInitialLoad.current) {
        const timer = setTimeout(() => {
          container.scrollTo({
            top: container.scrollHeight,
            behavior: isInitialLoad.current ? 'auto' : 'smooth'
          });
          if (debugLogs.length > 0) {
            isInitialLoad.current = false;
          }
        }, 50);
        return () => clearTimeout(timer);
      }
    }
  }, [debugLogs, debugMode]);

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'SUCCESS':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
            <CheckCircle2 size={12} /> {t('success')}
          </span>
        );
      case 'RUNNING':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
            <RefreshCw size={12} className="animate-spin" /> {t('running')}
          </span>
        );
      case 'FAILED':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-rose-500/10 text-rose-400 border border-rose-500/20">
            <AlertCircle size={12} /> {t('failed')}
          </span>
        );
      case 'PENDING':
      default:
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-yellow-500/10 text-yellow-400 border border-yellow-500/20">
            <RefreshCw size={12} /> {t('pending')}
          </span>
        );
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-zinc-50 flex items-center gap-2">
            <Terminal size={24} className="text-indigo-400" />
            {isKiosk ? (t('kioskLogsTitle') || 'Kiosk System Logs') : t('systemLogsTitle')}
          </h2>
          <p className="text-sm text-zinc-400">
            {isKiosk ? (t('kioskLogsSub') || 'Monitor real-time client kiosk operations.') : t('systemLogsSub')}
          </p>
        </div>

        {/* Toggle Switch */}
        <div className="flex items-center gap-3 bg-zinc-950 p-1.5 px-3 rounded-lg border border-zinc-800 self-stretch sm:self-auto justify-between">
          <span className="text-xs font-bold text-zinc-400 uppercase tracking-wider">{t('debugView')}</span>
          <button
            onClick={() => setDebugMode(!debugMode)}
            className={`w-10 h-6 rounded-full p-1 transition-colors outline-none focus:outline-none ${debugMode ? 'bg-indigo-600' : 'bg-zinc-800'}`}
          >
            <div className={`w-4 h-4 rounded-full bg-white transition-transform ${debugMode ? 'translate-x-4' : 'translate-x-0'}`} />
          </button>
        </div>
      </div>

      {!debugMode ? (
        <>
          <div className="relative">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
            <input
              type="text"
              placeholder={t('searchLogs')}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-9 pr-4 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm placeholder-zinc-500 focus:border-indigo-500 focus:outline-none"
            />
          </div>

          <div className="overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-900/50 backdrop-blur-md">
            <table className="min-w-full divide-y divide-zinc-800 text-left text-sm text-zinc-300">
              <thead className="bg-zinc-900 text-xs uppercase tracking-wider text-zinc-400">
                <tr>
                  <th className="px-4 py-2.5">{t('taskId')}</th>
                  <th className="px-4 py-2.5">{t('taskType')}</th>
                  <th className="px-4 py-2.5">{t('statusColumn')}</th>
                  <th className="px-4 py-2.5">{t('timestampColumn')}</th>
                  <th className="px-4 py-2.5 text-right">{t('actions')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800">
                {loading ? (
                  <tr>
                    <td colSpan={5} className="px-4 py-8 text-center text-zinc-500">{t('loading')}</td>
                  </tr>
                ) : tasks.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="px-4 py-8 text-center text-zinc-500">{t('noTasksFound')}</td>
                  </tr>
                ) : (
                  tasks.map(task => (
                    <tr key={task.id} className="hover:bg-zinc-800/30 transition-colors">
                      <td className="px-4 py-2.5 font-mono text-xs text-zinc-400">{task.id}</td>
                      <td className="px-4 py-2.5 font-semibold text-zinc-100 capitalize">{task.task_type.toLowerCase()}</td>
                      <td className="px-4 py-2.5">{getStatusBadge(task.status)}</td>
                      <td className="px-4 py-2.5 text-zinc-400">
                        {formatDate(task.created_at, timezone)}
                      </td>
                      <td className="px-4 py-2.5 text-right">
                        <button
                          onClick={() => onViewLogs(task.id, `${task.task_type} Task: ${task.id.slice(0, 8)}`)}
                          className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-semibold bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-400 rounded border border-indigo-500/20 transition-colors"
                        >
                          <Eye size={12} /> {t('viewLogs')}
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>

            {/* Pagination Footer */}
            {!loading && totalTasks > 0 && (
              <div className="px-4 py-3 bg-zinc-950/40 border-t border-zinc-800 flex flex-wrap justify-between items-center text-xs text-zinc-400 gap-3">
                <div>
                  {t('showingLabel') || 'Showing'}{' '}
                  <span className="text-zinc-200">{totalTasks === 0 ? 0 : (page - 1) * limit + 1}</span>{' '}
                  {t('toLabel') || 'to'}{' '}
                  <span className="text-zinc-200">{Math.min(page * limit, totalTasks)}</span>{' '}
                  {t('ofLabel') || 'of'}{' '}
                  <span className="text-zinc-200">{totalTasks}</span>{' '}
                  {t('tasksLabel') || 'tasks'}
                </div>
                
                <div className="flex items-center gap-4">
                  <div className="flex items-center gap-2">
                    <span>{t('rowsPerPage') || 'Rows per page'}:</span>
                    <select
                      value={limit}
                      onChange={(e) => {
                        setLimit(Number(e.target.value));
                        setPage(1);
                      }}
                      className="bg-zinc-950 border border-zinc-800 rounded px-1.5 py-1 text-zinc-200 text-xs focus:outline-none focus:border-indigo-500 cursor-pointer"
                    >
                      {[10, 20, 50, 100].map(val => (
                        <option key={val} value={val}>{val}</option>
                      ))}
                    </select>
                  </div>

                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => setPage(prev => Math.max(1, prev - 1))}
                      disabled={page === 1}
                      className="px-2.5 py-1.5 bg-zinc-900 border border-zinc-800 rounded hover:border-zinc-700 text-zinc-300 disabled:opacity-40 disabled:cursor-not-allowed transition-all cursor-pointer flex items-center gap-1"
                    >
                      <ChevronLeft size={14} />
                      {t('prev') || 'Previous'}
                    </button>
                    <span className="px-3 text-zinc-300">
                      {t('pageLabel') || 'Page'} {page} {t('ofLabel') || 'of'} {totalPages}
                    </span>
                    <button
                      type="button"
                      onClick={() => setPage(prev => Math.min(totalPages, prev + 1))}
                      disabled={page >= totalPages}
                      className="px-2.5 py-1.5 bg-zinc-900 border border-zinc-800 rounded hover:border-zinc-700 text-zinc-300 disabled:opacity-40 disabled:cursor-not-allowed transition-all cursor-pointer flex items-center gap-1"
                    >
                      {t('next') || 'Next'}
                      <ChevronRight size={14} />
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>
        </>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-bold text-zinc-500 uppercase tracking-wider flex items-center gap-1.5">
              <ShieldAlert size={14} className="text-amber-500" />
              {isKiosk ? (t('kioskLogsHeader') || 'Kiosk Backend Log Output') : t('liveOrchestratorLogs')}
            </span>
            {loadingDebug && <RefreshCw size={12} className="animate-spin text-zinc-500" />}
          </div>
          <div 
            ref={terminalContainerRef}
            className="bg-zinc-950 border border-zinc-800 rounded-xl p-4 font-mono text-xs overflow-y-auto h-[500px] flex flex-col space-y-1 scrollbar-thin scrollbar-thumb-zinc-800"
          >
            {debugLogs.length === 0 ? (
              <span className="text-zinc-500">{isKiosk ? (t('kioskWaitingLogs') || 'Waiting for log records...') : t('waitingLogs')}</span>
            ) : (
              debugLogs.map((log) => {
                let colorClass = "text-zinc-300";
                if (log.level === "ERROR") colorClass = "text-rose-600 dark:text-rose-400 font-bold";
                else if (log.level === "WARNING") colorClass = "text-amber-600 dark:text-yellow-400 font-medium";
                else if (log.level === "DEBUG") colorClass = "text-zinc-500";
                const timeStr = formatDate(log.created_at, timezone);
                const cleanMsg = log.message.replace(/^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:,\d+)?\s*/, '');
                return (
                  <div key={log.id} className={`${colorClass} break-all whitespace-pre-wrap`}>
                    <span className="text-zinc-500 mr-2">{timeStr}</span>
                    {cleanMsg}
                  </div>
                );
              })
            )}
            <div ref={terminalEndRef} />
          </div>
        </div>
      )}
    </div>
  );
}
