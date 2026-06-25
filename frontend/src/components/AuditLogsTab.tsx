import React, { useState, useEffect } from 'react';
import { Search, RefreshCw, ScrollText, Loader2, ChevronLeft, ChevronRight, AlertCircle } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';
import { formatDate } from './dateUtils';

interface AuditLog {
  id: number;
  username: string;
  action: string;
  details: string | null;
  ip_address: string | null;
  created_at: string;
}

interface AuditLogsTabProps {
  timezone?: string;
  type?: 'admin' | 'kiosk';
}

export default function AuditLogsTab({ timezone, type }: AuditLogsTabProps) {
  const { t } = useTranslation();
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [searchTerm, setSearchTerm] = useState('');
  const [currentPage, setCurrentPage] = useState(1);
  const itemsPerPage = 30;

  const fetchLogs = async () => {
    setLoading(true);
    setError('');
    try {
      const url = `/api/users/audit-logs${type ? `?type=${type}` : ''}`;
      const res = await fetch(url);
      if (res.ok) {
        const data = await res.json();
        setLogs(data);
      } else {
        const errData = await res.json();
        setError(errData.detail || 'Failed to fetch audit logs');
      }
    } catch (e) {
      console.error(e);
      setError('Connection error. Failed to retrieve audit logs.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchLogs();
  }, []);

  const handleSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setSearchTerm(e.target.value);
    setCurrentPage(1);
  };

  // Filter logs locally
  const filteredLogs = logs.filter(log => {
    const searchLower = searchTerm.toLowerCase();
    const username = (log.username || '').toLowerCase();
    const action = (log.action || '').toLowerCase();
    const details = (log.details || '').toLowerCase();
    const ip = (log.ip_address || '').toLowerCase();
    return username.includes(searchLower) || 
           action.includes(searchLower) || 
           details.includes(searchLower) ||
           ip.includes(searchLower);
  });

  // Pagination
  const totalPages = Math.ceil(filteredLogs.length / itemsPerPage) || 1;
  const startIndex = (currentPage - 1) * itemsPerPage;
  const paginatedLogs = filteredLogs.slice(startIndex, startIndex + itemsPerPage);

  const getActionBadgeColor = (action: string) => {
    const act = action.toLowerCase();
    if (act.includes('fail') || act.includes('delete') || act.includes('block') || act.includes('revoke') || act.includes('purge')) {
      return 'bg-rose-500/10 text-rose-400 border-rose-500/20';
    }
    if (act.includes('create') || act.includes('register') || act.includes('login') || act.includes('handshake approved') || act.includes('activation approved')) {
      return 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20';
    }
    if (act.includes('update') || act.includes('toggle') || act.includes('queue') || act.includes('change') || act.includes('edit')) {
      return 'bg-amber-500/10 text-amber-400 border-amber-500/20';
    }
    return 'bg-indigo-500/10 text-indigo-400 border-indigo-500/20';
  };

  const renderDetailsCell = (details: string | null) => {
    if (!details) return <span className="text-zinc-650">—</span>;

    const isDiff = details.includes('➔');
    let header = "";
    let items: string[] = [];
    
    if (isDiff) {
      const colonIndex = details.indexOf(':');
      if (colonIndex !== -1) {
        header = details.substring(0, colonIndex).trim();
        const changesStr = details.substring(colonIndex + 1).trim();
        items = changesStr.split(/,\s*(?![^()]*\))/);
      } else {
        items = [details];
      }
    } else {
      items = [details];
    }

    const formatItem = (item: string) => {
      if (item.includes('➔')) {
        const parts = item.split('➔');
        return (
          <>
            <span className="text-zinc-300 font-medium">{parts[0]}</span>
            <span className="text-indigo-400 font-bold px-1.5 text-[11px]">➔</span>
            <span className="text-emerald-400 font-medium">{parts[1]}</span>
          </>
        );
      }
      return <span className="text-zinc-300">{item}</span>;
    };

    return (
      <div className="relative group cursor-help py-1">
        <span className="truncate block max-w-xs sm:max-w-md text-zinc-300">{details}</span>
        
        {/* Premium Glassmorphic Tooltip */}
        <div className="absolute left-1/2 -translate-x-1/2 bottom-full mb-2 w-80 p-3 bg-zinc-950/95 backdrop-blur-md border border-zinc-800 rounded-xl shadow-2xl opacity-0 scale-95 pointer-events-none group-hover:opacity-100 group-hover:scale-100 transition-all duration-200 z-50 transform translate-y-1 group-hover:translate-y-0 text-left">
          {header && (
            <div className="text-[10px] font-bold text-indigo-400 uppercase tracking-wider mb-1.5 border-b border-zinc-850 pb-1">
              {header}
            </div>
          )}
          <ul className="space-y-1 text-[11px] leading-relaxed max-h-48 overflow-y-auto pr-1">
            {items.map((item, idx) => (
              <li key={idx} className="flex items-start gap-1">
                <span className="text-zinc-500 mt-0.5">•</span>
                <span className="break-all">{formatItem(item)}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    );
  };

  return (
    <div className="space-y-6">
      {/* Header and Refresh Button */}
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-zinc-50 flex items-center gap-2">
            <ScrollText size={22} className="text-indigo-400" />
            {type === 'kiosk' ? (t('tabKioskLogs') || 'Kiosk Logs') : (t('tabAuditLogs') || 'Audit Logs')}
          </h2>
          <p className="text-xs text-zinc-400 mt-1">
            {type === 'kiosk'
              ? (t('kioskActionLogsSub') || 'Monitor connected kiosk handshakes and archive downloads.')
              : (t('auditLogsSub') || 'Monitor administrative actions and user login attempts.')}
          </p>
        </div>
        <button
          onClick={fetchLogs}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 disabled:opacity-50 text-zinc-200 border border-zinc-700 hover:border-zinc-600 rounded-lg font-bold text-xs tracking-wide shadow transition-colors cursor-pointer"
        >
          <RefreshCw size={12} className={loading ? 'animate-spin' : ''} /> 
          {t('refresh') || 'Refresh'}
        </button>
      </div>

      {/* Filter Toolbar */}
      <div className="flex flex-col sm:flex-row gap-3">
        <div className="relative flex-1">
          <span className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-zinc-500">
            <Search size={16} />
          </span>
          <input
            type="text"
            placeholder={t('searchLogsPlaceholder') || 'Search logs...'}
            value={searchTerm}
            onChange={handleSearchChange}
            className="w-full pl-9 pr-4 py-2 bg-zinc-900 border border-zinc-800 rounded-lg text-zinc-100 text-xs placeholder-zinc-500 focus:border-indigo-500 focus:outline-none transition-colors"
          />
        </div>
      </div>

      {error && (
        <div className="p-4 bg-rose-500/10 border border-rose-500/20 text-rose-400 text-xs rounded-xl flex items-center gap-2">
          <AlertCircle size={16} />
          <span>{error}</span>
        </div>
      )}

      {/* Audit Logs Table */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-2xl shadow-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="border-b border-zinc-800 bg-zinc-950/50 text-zinc-400 font-bold">
                <th className="p-4 w-44">{t('timestampColumn') || 'Timestamp'}</th>
                <th className="p-4 w-40">{t('auditTableUser') || 'User'}</th>
                <th className="p-4 w-52">{t('auditTableAction') || 'Action'}</th>
                <th className="p-4">{t('auditTableDetails') || 'Details'}</th>
                <th className="p-4 w-36">{t('auditTableIp') || 'IP Address'}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-850">
              {loading && logs.length === 0 ? (
                <tr>
                  <td colSpan={5} className="p-8 text-center text-zinc-500">
                    <div className="flex flex-col items-center gap-2 justify-center">
                      <Loader2 className="animate-spin text-indigo-500" size={24} />
                      <span>{t('loading') || 'Loading logs...'}</span>
                    </div>
                  </td>
                </tr>
              ) : paginatedLogs.length === 0 ? (
                <tr>
                  <td colSpan={5} className="p-8 text-center text-zinc-500">
                    {t('noAuditLogsFound') || 'No audit logs found.'}
                  </td>
                </tr>
              ) : (
                paginatedLogs.map((log) => (
                  <tr key={log.id} className="hover:bg-zinc-850/30 text-zinc-300 transition-colors">
                    <td className="p-4 font-mono text-zinc-400 whitespace-nowrap">
                      {formatDate(log.created_at, timezone)}
                    </td>
                    <td className="p-4 font-semibold text-zinc-200 truncate max-w-[160px]" title={log.username}>
                      {log.username}
                    </td>
                    <td className="p-4">
                      <span className={`px-2 py-0.5 rounded text-[10px] font-bold border ${getActionBadgeColor(log.action)}`}>
                        {log.action}
                      </span>
                    </td>
                    <td className="p-4 text-zinc-400 break-words max-w-md relative">
                      {renderDetailsCell(log.details)}
                    </td>
                    <td className="p-4 font-mono text-zinc-400 whitespace-nowrap">
                      {log.ip_address || <span className="text-zinc-650">—</span>}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination Footer */}
        {totalPages > 1 && (
          <div className="px-4 py-3 bg-zinc-950/40 border-t border-zinc-800 flex justify-between items-center text-xs">
            <span className="text-zinc-400">
              {t('page') || 'Page'} {currentPage} {t('of') || 'of'} {totalPages}
            </span>
            <div className="flex gap-2">
              <button
                onClick={() => setCurrentPage(prev => Math.max(prev - 1, 1))}
                disabled={currentPage === 1}
                className="p-1.5 bg-zinc-800 hover:bg-zinc-700 disabled:opacity-50 text-zinc-300 border border-zinc-700 rounded-lg cursor-pointer transition-colors"
              >
                <ChevronLeft size={16} />
              </button>
              <button
                onClick={() => setCurrentPage(prev => Math.min(prev + 1, totalPages))}
                disabled={currentPage === totalPages}
                className="p-1.5 bg-zinc-800 hover:bg-zinc-700 disabled:opacity-50 text-zinc-300 border border-zinc-700 rounded-lg cursor-pointer transition-colors"
              >
                <ChevronRight size={16} />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
