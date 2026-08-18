import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Bell, ShieldAlert, AlertTriangle, Check, Loader2 } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';
import { formatDate } from './dateUtils';

interface Alert {
  id: number;
  module: string;
  node_id: number | null;
  node_hostname: string | null;
  severity: 'WATCH' | 'ALERT';
  status: 'OPEN' | 'ACKNOWLEDGED' | 'RESOLVED';
  title: string;
  first_seen: string;
  last_seen: string;
  acknowledged_by: string | null;
}

//: Alerts are re-evaluated hourly server-side, so sub-minute polling here
//: would only ever show the same data faster. This matches that cadence
//: closely enough to feel live without hammering the API.
const POLL_MS = 60000;

interface NotificationBellProps {
  timezone?: string;
}

export default function NotificationBell({ timezone }: NotificationBellProps) {
  const { t } = useTranslation();
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [isOpen, setIsOpen] = useState(false);
  const [ackingId, setAckingId] = useState<number | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const fetchAlerts = useCallback(async () => {
    try {
      const res = await fetch('/api/alerts?status=OPEN');
      if (res.ok) {
        setAlerts(await res.json());
      }
    } catch (err) {
      console.error('Failed to fetch alerts:', err);
    }
  }, []);

  useEffect(() => {
    fetchAlerts();
    const interval = setInterval(fetchAlerts, POLL_MS);
    return () => clearInterval(interval);
  }, [fetchAlerts]);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleAcknowledge = async (id: number) => {
    setAckingId(id);
    try {
      const res = await fetch(`/api/alerts/${id}/acknowledge`, { method: 'POST' });
      if (res.ok) {
        setAlerts((prev) => prev.filter((a) => a.id !== id));
      }
    } catch (err) {
      console.error('Failed to acknowledge alert:', err);
    } finally {
      setAckingId(null);
    }
  };

  const count = alerts.length;

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="relative flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-zinc-950 hover:bg-zinc-900 border border-zinc-800 text-[11px] text-zinc-300 font-bold transition-all duration-200 cursor-pointer outline-none"
        title={t('notificationsBellTitle')}
      >
        <Bell size={12} className={count > 0 ? 'text-amber-400' : 'text-zinc-400'} />
        {count > 0 && (
          <span className="inline-flex items-center justify-center min-w-[15px] h-3.5 px-1 rounded-full bg-rose-500 text-white text-[9px] font-bold leading-none">
            {count > 99 ? '99+' : count}
          </span>
        )}
      </button>

      {isOpen && (
        <div className="absolute right-0 mt-1.5 w-80 max-h-96 overflow-y-auto rounded-lg bg-zinc-900 border border-zinc-800 shadow-2xl p-1 z-50 origin-top-right animate-dropdown-in">
          {alerts.length === 0 ? (
            <div className="px-3 py-6 text-center text-xs text-zinc-500 font-semibold">
              {t('notificationsEmpty')}
            </div>
          ) : (
            alerts.map((alert) => (
              <div
                key={alert.id}
                className="flex items-start gap-2 px-3 py-2.5 rounded-md hover:bg-zinc-800/60 transition-colors"
              >
                <div className={`mt-0.5 ${alert.severity === 'ALERT' ? 'text-rose-400' : 'text-amber-400'}`}>
                  {alert.severity === 'ALERT' ? <ShieldAlert size={14} /> : <AlertTriangle size={14} />}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-bold text-zinc-100 leading-tight">{alert.title}</p>
                  <div className="flex items-center gap-2 mt-0.5">
                    {alert.node_hostname && (
                      <p className="text-[10px] text-zinc-500 font-mono">{alert.node_hostname}</p>
                    )}
                    <p className="text-[10px] text-zinc-600">{formatDate(alert.first_seen, timezone)}</p>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => handleAcknowledge(alert.id)}
                  disabled={ackingId === alert.id}
                  title={t('notificationsAck')}
                  className="p-1.5 rounded-md text-zinc-500 hover:text-emerald-400 hover:bg-emerald-500/10 transition-colors cursor-pointer disabled:opacity-50"
                >
                  {ackingId === alert.id ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
                </button>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
