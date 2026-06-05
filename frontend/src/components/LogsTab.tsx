import React, { useState, useEffect, useRef } from 'react';
import { Terminal, Search, CheckCircle2, AlertCircle, RefreshCw, Eye, ShieldAlert } from 'lucide-react';

interface TaskLog {
  id: string;
  task_type: string;
  status: string;
  created_at: string;
  updated_at: string;
}

interface SystemLog {
  id: number;
  level: string;
  message: string;
  created_at: string;
}

interface LogsTabProps {
  onViewLogs: (taskId: string, title: string) => void;
}

export default function LogsTab({ onViewLogs }: LogsTabProps) {
  const [tasks, setTasks] = useState<TaskLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');

  // Debug Mode State
  const [debugMode, setDebugMode] = useState(false);
  const [debugLogs, setDebugLogs] = useState<SystemLog[]>([]);
  const [loadingDebug, setLoadingDebug] = useState(false);
  const terminalEndRef = useRef<HTMLDivElement>(null);

  const fetchTasks = async () => {
    try {
      const res = await fetch('/api/tasks');
      if (res.ok) {
        const data = await res.json();
        setTasks(data);
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
  }, [debugMode]);

  // Scroll to bottom when new logs arrive in debug mode
  useEffect(() => {
    if (debugMode && terminalEndRef.current) {
      terminalEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [debugLogs, debugMode]);

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'SUCCESS':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
            <CheckCircle2 size={12} /> Success
          </span>
        );
      case 'RUNNING':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
            <RefreshCw size={12} className="animate-spin" /> Running
          </span>
        );
      case 'FAILED':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-rose-500/10 text-rose-400 border border-rose-500/20">
            <AlertCircle size={12} /> Failed
          </span>
        );
      case 'PENDING':
      default:
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-yellow-500/10 text-yellow-400 border border-yellow-500/20">
            <RefreshCw size={12} /> Pending
          </span>
        );
    }
  };

  const filteredTasks = tasks.filter(task => {
    const q = searchQuery.toLowerCase();
    return (
      task.id.toLowerCase().includes(q) ||
      task.task_type.toLowerCase().includes(q) ||
      task.status.toLowerCase().includes(q)
    );
  });

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
            <Terminal size={24} className="text-indigo-400" />
            System Logs & Tasks
          </h2>
          <p className="text-sm text-zinc-400">View execution logs and statuses of all background tasks.</p>
        </div>

        {/* Toggle Switch */}
        <div className="flex items-center gap-3 bg-zinc-950 p-1.5 px-3 rounded-lg border border-zinc-800 self-stretch sm:self-auto justify-between">
          <span className="text-xs font-bold text-zinc-400 uppercase tracking-wider">Debug View</span>
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
              placeholder="Filter by Task ID, Type, or Status..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-9 pr-4 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-white text-sm placeholder-zinc-500 focus:border-indigo-500 focus:outline-none"
            />
          </div>

          <div className="overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-900/50 backdrop-blur-md">
            <table className="min-w-full divide-y divide-zinc-800 text-left text-sm text-zinc-300">
              <thead className="bg-zinc-900 text-xs uppercase tracking-wider text-zinc-400">
                <tr>
                  <th className="px-4 py-2.5">Task ID</th>
                  <th className="px-4 py-2.5">Task Type</th>
                  <th className="px-4 py-2.5">Status</th>
                  <th className="px-4 py-2.5">Created At</th>
                  <th className="px-4 py-2.5 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800">
                {loading ? (
                  <tr>
                    <td colSpan={5} className="px-4 py-8 text-center text-zinc-500">Loading system logs...</td>
                  </tr>
                ) : filteredTasks.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="px-4 py-8 text-center text-zinc-500">No tasks found.</td>
                  </tr>
                ) : (
                  filteredTasks.map(task => (
                    <tr key={task.id} className="hover:bg-zinc-800/30 transition-colors">
                      <td className="px-4 py-2.5 font-mono text-xs text-zinc-400">{task.id}</td>
                      <td className="px-4 py-2.5 font-semibold text-white capitalize">{task.task_type.toLowerCase()}</td>
                      <td className="px-4 py-2.5">{getStatusBadge(task.status)}</td>
                      <td className="px-4 py-2.5 text-zinc-400">
                        {new Date(task.created_at).toLocaleString()}
                      </td>
                      <td className="px-4 py-2.5 text-right">
                        <button
                          onClick={() => onViewLogs(task.id, `${task.task_type} Task: ${task.id.slice(0, 8)}`)}
                          className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-semibold bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-400 rounded border border-indigo-500/20 transition-colors"
                        >
                          <Eye size={12} /> View Logs
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-bold text-zinc-500 uppercase tracking-wider flex items-center gap-1.5">
              <ShieldAlert size={14} className="text-amber-500" />
              Live orchestrator backend log output (Last 200 lines)
            </span>
            {loadingDebug && <RefreshCw size={12} className="animate-spin text-zinc-500" />}
          </div>
          <div className="bg-zinc-950 border border-zinc-800 rounded-xl p-4 font-mono text-xs overflow-y-auto h-[500px] flex flex-col space-y-1 scrollbar-thin scrollbar-thumb-zinc-800">
            {debugLogs.length === 0 ? (
              <span className="text-zinc-500">Waiting for log records... Make sure orchestrator is active.</span>
            ) : (
              debugLogs.map((log) => {
                let colorClass = "text-zinc-300";
                if (log.level === "ERROR") colorClass = "text-rose-400 font-bold";
                else if (log.level === "WARNING") colorClass = "text-yellow-400 font-medium";
                else if (log.level === "DEBUG") colorClass = "text-zinc-500";
                return (
                  <div key={log.id} className={`${colorClass} break-all whitespace-pre-wrap`}>
                    {log.message}
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
