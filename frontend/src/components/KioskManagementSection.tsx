import React, { useState, useEffect } from 'react';
import { Plus, Trash2, ShieldAlert, CheckCircle, RefreshCw, Clipboard, Copy, Server, Globe, Search, Edit2 } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';
import { formatDate } from './dateUtils';

interface Kiosk {
  id: number;
  name: string | null;
  kiosk_id: string;
  key: string;
  status: 'PENDING' | 'APPROVED' | 'REVOKED' | 'DISABLED';
  ip_address: string | null;
  ssh_pub_key: string | null;
  created_at: string;
  updated_at: string;
  approved_at?: string | null;
  last_seen?: string | null;
  is_online?: boolean;
  contact: string | null;
  comment: string | null;
  iso_exists?: boolean;
  auth_token?: string | null;
  target_ip: string | null;
  rebuild_required: boolean;
  iso_built_at?: string | null;
  is_rebuilding?: boolean;
  payload_outdated?: boolean;
}

interface KioskManagementSectionProps {
  onViewLogs?: (taskId: string, title: string) => void;
  baseIsoCreatedAt?: string | null;
}

export default function KioskManagementSection({ onViewLogs, baseIsoCreatedAt }: KioskManagementSectionProps) {
  const { t, language } = useTranslation();
  const [kiosks, setKiosks] = useState<Kiosk[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAddModal, setShowAddModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [showKeyModal, setShowKeyModal] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');

  const filteredKiosks = kiosks.filter(k => {
    const query = searchQuery.toLowerCase();
    const nameMatch = (k.name || '').toLowerCase().includes(query);
    const uuidMatch = (k.kiosk_id || '').toLowerCase().includes(query);
    const ipMatch = (k.ip_address || '').toLowerCase().includes(query);
    const contactMatch = (k.contact || '').toLowerCase().includes(query);
    const commentMatch = (k.comment || '').toLowerCase().includes(query);
    
    // Check both raw status and translated status names if possible
    const statusMatch = (k.status || '').toLowerCase().includes(query);
    
    return nameMatch || uuidMatch || ipMatch || statusMatch || contactMatch || commentMatch;
  });
  
  // Form fields
  const [name, setName] = useState('');
  const [kioskId, setKioskId] = useState('');
  const [contact, setContact] = useState('');
  const [comment, setComment] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  
  // Edit form states
  const [editingKiosk, setEditingKiosk] = useState<Kiosk | null>(null);
  const [editName, setEditName] = useState('');
  const [editContact, setEditContact] = useState('');
  const [editComment, setEditComment] = useState('');

  // Update Target IP Modal state
  const [showIpModal, setShowIpModal] = useState(false);
  const [targetIpToUpdate, setTargetIpToUpdate] = useState('');
  const [ipKioskId, setIpKioskId] = useState<number | null>(null);

  // Key display modal state
  const [generatedKey, setGeneratedKey] = useState('');
  const [copied, setCopied] = useState(false);

  const handleUpdateIpClick = (kiosk: Kiosk) => {
    setIpKioskId(kiosk.id);
    setTargetIpToUpdate(kiosk.target_ip || '');
    setError('');
    setShowIpModal(true);
  };

  const handleUpdateIpSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!ipKioskId) return;
    setSubmitting(true);
    setError('');
    try {
      const res = await fetch(`/api/iso/kiosks/${ipKioskId}/update_ip`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target_ip: targetIpToUpdate.trim()
        })
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Failed to update kiosk target IP');
      }
      setShowIpModal(false);
      setIpKioskId(null);
      fetchKiosks();
      window.dispatchEvent(new CustomEvent('kiosks-updated'));
    } catch (err: any) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  const fetchKiosks = async () => {
    try {
      const res = await fetch('/api/kiosks');
      if (res.ok) {
        const data = await res.json();
        setKiosks(data);
      }
    } catch (err) {
      console.error('Failed to fetch kiosks:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchKiosks();
    const interval = setInterval(fetchKiosks, 5000);
    
    const handleUpdateEvent = () => {
      fetchKiosks();
    };
    window.addEventListener('kiosks-updated', handleUpdateEvent);
    
    return () => {
      clearInterval(interval);
      window.removeEventListener('kiosks-updated', handleUpdateEvent);
    };
  }, []);

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      const res = await fetch('/api/kiosks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name || null,
          kiosk_id: kioskId.trim() || null,
          contact: contact.trim() || null,
          comment: comment.trim() || null
        })
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Registration failed');
      }
      
      setGeneratedKey(data.key);
      setShowAddModal(false);
      setShowKeyModal(true);
      setName('');
      setKioskId('');
      setContact('');
      setComment('');
      fetchKiosks();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleEditClick = (kiosk: Kiosk) => {
    setEditingKiosk(kiosk);
    setEditName(kiosk.name || '');
    setEditContact(kiosk.contact || '');
    setEditComment(kiosk.comment || '');
    setError('');
    setShowEditModal(true);
  };

  const handleEditSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingKiosk) return;
    setSubmitting(true);
    setError('');
    try {
      const res = await fetch(`/api/kiosks/${editingKiosk.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: editName.trim() || null,
          contact: editContact.trim() || null,
          comment: editComment.trim() || null
        })
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Failed to update kiosk');
      }
      setShowEditModal(false);
      setEditingKiosk(null);
      fetchKiosks();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleRevoke = async (id: number) => {
    if (window.confirm(t('kioskRevokeConfirm') || 'Are you sure you want to revoke access for this kiosk? Its SSH credentials will be disabled.')) {
      try {
        const res = await fetch(`/api/kiosks/${id}/revoke`, { method: 'POST' });
        if (res.ok) {
          fetchKiosks();
        }
      } catch (err) {
        console.error(err);
      }
    }
  };

  const handleToggleActive = async (id: number) => {
    try {
      const res = await fetch(`/api/kiosks/${id}/toggle-active`, { method: 'POST' });
      if (res.ok) {
        fetchKiosks();
      } else {
        const data = await res.json();
        alert(data.detail || 'Failed to toggle active state');
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleRecreateIso = async (id: number) => {
    try {
      const res = await fetch(`/api/iso/kiosks/${id}/recreate`, { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        if (data.task_id && onViewLogs) {
          onViewLogs(data.task_id, t('issueKioskGenerating') || 'Repackaging ISO image...');
        } else {
          alert(t('issueKioskGenerating') || 'ISO regeneration task started.');
        }
        fetchKiosks();
      } else {
        const data = await res.json();
        alert(data.detail || 'Failed to start ISO recreation');
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleDelete = async (id: number) => {
    if (window.confirm(t('deleteConfirm') || 'Are you sure you want to delete this record?')) {
      try {
        const res = await fetch(`/api/kiosks/${id}`, { method: 'DELETE' });
        if (res.ok) {
          fetchKiosks();
        }
      } catch (err) {
        console.error(err);
      }
    }
  };

  const copyKeyToClipboard = () => {
    navigator.clipboard.writeText(generatedKey);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="space-y-6">
      {/* Header section */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 bg-zinc-900/50 p-4 border border-zinc-800 rounded-2xl">
        <div>
          <h3 className="text-base font-bold text-zinc-50">{t('kioskControlPanel') || 'Kiosk Control Panel'}</h3>
          <p className="text-xs text-zinc-400 mt-1">{t('kioskControlSub') || 'Manage connection keys and authorization status for Live-CD technician kiosk clients.'}</p>
        </div>
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3 w-full sm:w-auto">
          <div className="relative w-full sm:w-64">
            <input
              type="text"
              placeholder={t('searchKiosksPlaceholder') || 'Search kiosks by name, UUID, IP...'}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-9 pr-3 py-1.5 bg-zinc-950 border border-zinc-800 hover:border-zinc-700 focus:border-indigo-500 focus:outline-none rounded-lg text-xs text-zinc-100 placeholder-zinc-500 transition-colors"
            />
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" size={14} />
          </div>

        </div>
      </div>

      {/* Table grid */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-2xl shadow-xl overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-12 text-zinc-500">
            <RefreshCw className="animate-spin mr-2" size={18} />
            <span>{t('loading') || 'Loading...'}</span>
          </div>
        ) : kiosks.length === 0 ? (
          <div className="py-12 text-center text-zinc-500">
            <Server className="mx-auto text-zinc-600 mb-3" size={36} />
            <p className="text-xs font-semibold">{t('noKiosksFound') || 'No registered kiosks found'}</p>
            <p className="text-[10px] text-zinc-500 mt-1">{t('registerKioskHint') || 'Click "Register Kiosk" to generate a pairing key.'}</p>
          </div>
        ) : filteredKiosks.length === 0 ? (
          <div className="py-12 text-center text-zinc-500">
            <Search className="mx-auto text-zinc-600 mb-3" size={36} />
            <p className="text-xs font-semibold">{t('noMatchingKiosks') || 'No matching kiosks found'}</p>
            <p className="text-[10px] text-zinc-500 mt-1">{t('adjustSearchCriteria') || 'Try adjusting your search criteria'}</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left text-xs">
              <thead>
                <tr className="border-b border-zinc-800 text-zinc-400 font-semibold bg-zinc-950/40">
                  <th className="py-1.5 px-2.5">{t('kioskNameLabel') || 'Name'}</th>
                  <th className="py-1.5 px-2.5">{t('kioskUuidLabel') || 'UUID'}</th>
                  <th className="py-1.5 px-2.5">{t('statusLabel') || 'Status'}</th>
                  <th className="py-1.5 px-2.5">{t('kioskApprovedAtLabel') || 'Approval Date'}</th>
                  <th className="py-1.5 px-2.5">{t('kioskIsoBuiltAtLabel') || 'ISO Built'}</th>
                  <th className="py-1.5 px-2.5 text-right">{t('actionsLabel') || 'Actions'}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800/60">
                {filteredKiosks.map((kiosk) => (
                  <tr key={kiosk.id} className="hover:bg-zinc-950/20 transition-colors">
                    <td className="py-2 px-2.5">
                      <div className="font-bold text-zinc-200">
                        {kiosk.name || <span className="text-zinc-500 italic">{t('unnamedKiosk') || 'Unnamed Kiosk'}</span>}
                      </div>
                      
                      {/* Target IP & Rebuild badge */}
                      <div className="text-[10px] mt-1 flex flex-wrap items-center gap-1.5">
                        {kiosk.target_ip && (
                          <span className="inline-flex items-center gap-1 text-zinc-400 bg-zinc-950 px-1.5 py-0.5 rounded border border-zinc-800/80">
                            <Server size={10} className="text-zinc-500" />
                            <span className="text-[9px] font-mono text-zinc-350">{kiosk.target_ip}</span>
                            <button
                              onClick={() => handleUpdateIpClick(kiosk)}
                              className="hover:text-indigo-400 transition-colors cursor-pointer"
                              title={t('changeTargetIp') || 'Change Target IP'}
                            >
                              <Edit2 size={10} className="text-zinc-500 hover:text-indigo-400 ml-0.5" />
                            </button>
                          </span>
                        )}
                      </div>
                      
                      {(kiosk.contact || kiosk.comment) && (
                        <div className="text-[10px] text-zinc-400 mt-1 space-y-0.5">
                          {kiosk.contact && (
                            <div className="flex items-center gap-1">
                              <span className="text-zinc-500 font-semibold">{t('kioskContact') || 'Contact'}:</span>
                              <span>{kiosk.contact}</span>
                            </div>
                          )}
                          {kiosk.comment && (
                            <div className="italic text-zinc-500 max-w-xs truncate" title={kiosk.comment}>
                              {kiosk.comment}
                            </div>
                          )}
                        </div>
                      )}
                    </td>
                    <td className="py-2 px-2.5 font-mono text-zinc-400 select-all">
                      <div className="relative group cursor-help inline-block">
                        <span>
                          {kiosk.kiosk_id.startsWith('PENDING-') 
                            ? <span className="text-zinc-500 italic">{t('kioskPending') || 'Pending...'}</span> 
                            : kiosk.kiosk_id}
                        </span>
                        <div className="absolute left-1/2 -translate-x-1/2 bottom-full mb-1.5 px-2 py-1 bg-zinc-950/95 backdrop-blur-md text-zinc-200 text-[10px] rounded-lg border border-zinc-800 shadow-2xl opacity-0 group-hover:opacity-100 transition-all duration-200 whitespace-nowrap pointer-events-none z-50 font-sans scale-95 group-hover:scale-100">
                          <span className="text-zinc-500 font-bold mr-1">
                            {t('kioskPairingKeyTooltip') || 'Pairing Key:'}
                          </span>
                          <span className="font-mono font-black text-indigo-400">
                            {kiosk.key}
                          </span>
                        </div>
                      </div>
                    </td>
                    <td className="py-2 px-2.5 font-semibold">
                      <div className="space-y-1">
                        <div>
                          {kiosk.status === 'APPROVED' ? (
                            kiosk.is_online ? (
                              <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20" title={kiosk.last_seen ? `Last seen: ${formatDate(kiosk.last_seen)}` : ''}>
                                <span className="relative flex h-2 w-2 mr-0.5">
                                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                                  <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
                                </span>
                                {t('kioskStatusApprovedLabel') || 'Active'}
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-bold bg-zinc-500/10 text-zinc-400 border border-zinc-500/20" title={kiosk.last_seen ? `Last seen: ${formatDate(kiosk.last_seen)}` : ''}>
                                <span className="relative flex h-2 w-2 mr-0.5">
                                  <span className="relative inline-flex rounded-full h-2 w-2 bg-zinc-500"></span>
                                </span>
                                {t('kioskStatusOfflineLabel') || 'Offline'}
                              </span>
                            )
                          ) : kiosk.status === 'DISABLED' ? (
                            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-bold bg-red-500/10 text-red-400 border border-red-500/20">
                              <ShieldAlert size={10} /> {t('kioskStatusDisabledLabel') || 'Disabled'}
                            </span>
                          ) : kiosk.status === 'PENDING' && kiosk.kiosk_id && !kiosk.kiosk_id.startsWith('PENDING-') ? (
                            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-bold bg-amber-500/10 text-amber-400 border border-amber-500/20 animate-pulse">
                              <ShieldAlert size={10} /> {t('kioskStatusPendingLabel') || 'Re-activation Request'}
                            </span>
                          ) : kiosk.status === 'REVOKED' ? (
                            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-bold bg-rose-500/10 text-rose-400 border border-rose-500/20">
                              <ShieldAlert size={10} /> {t('kioskRevoked') || 'Access Revoked'}
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-bold bg-amber-500/10 text-amber-400 border border-amber-500/20">
                              <RefreshCw size={10} className="animate-spin-slow" /> {t('kioskPending') || 'Pending Connection'}
                            </span>
                          )}
                        </div>
                        {kiosk.ip_address && (
                          <div className="flex items-center gap-1.5 text-[10px] text-zinc-350 font-mono font-bold leading-none">
                            <span>{kiosk.ip_address}</span>
                            {kiosk.payload_outdated && (
                              <span 
                                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[8px] font-bold bg-amber-500/10 text-amber-400 border border-amber-500/20 cursor-help select-none shrink-0"
                                title={t('updateRecommendedTooltip') || 'Kiosk client version is older than the server payload'}
                              >
                                {t('updateRecommendedBadge') || 'Update recommended'}
                              </span>
                            )}
                          </div>
                        )}
                        <div className="text-[9px] text-zinc-500 font-mono">
                          <span className="text-zinc-650 font-sans mr-0.5">{t('kioskCreatedAtLabel') || 'Built'}:</span>{' '}
                          {kiosk.created_at ? formatDate(kiosk.created_at) : '—'}
                        </div>
                      </div>
                    </td>
                    <td className="py-2 px-2.5 text-zinc-350 font-mono">
                      {kiosk.approved_at ? formatDate(kiosk.approved_at) : <span className="text-zinc-655">—</span>}
                    </td>
                    <td className="py-2 px-2.5">
                      {kiosk.iso_built_at ? (
                        <div className="space-y-0.5">
                          <div className="font-mono text-zinc-350">
                            {formatDate(kiosk.iso_built_at)}
                          </div>
                          {baseIsoCreatedAt && (() => {
                            // Normalize to UTC: backend stores iso_built_at as naive UTC (no 'Z'),
                            // JS new Date() without a TZ suffix treats it as LOCAL time — wrong.
                            // Append 'Z' if no timezone marker is present.
                            const normalizeUTC = (s: string) =>
                              /[Zz]$|[+-]\d{2}:\d{2}$/.test(s) ? s : s + 'Z';
                            const builtMs = new Date(normalizeUTC(kiosk.iso_built_at!)).getTime();
                            const baseMs = new Date(normalizeUTC(baseIsoCreatedAt)).getTime();
                            const isFresh = builtMs >= baseMs;
                            return isFresh ? (
                              <span
                                className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[9px] font-bold rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/25 uppercase"
                                title={t('isoFreshTooltip') || 'Built on current base ISO'}
                              >
                                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                                {t('isoFreshLabel') || 'Fresh'}
                              </span>
                            ) : (
                              <span
                                className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[9px] font-bold rounded bg-amber-500/15 text-amber-400 border border-amber-500/25 uppercase"
                                title={t('isoOldTooltip') || 'Built on an outdated base ISO — re-create recommended'}
                              >
                                <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
                                {t('isoOldLabel') || 'Outdated'}
                              </span>
                            );
                          })()}
                        </div>
                      ) : kiosk.is_rebuilding ? (
                        <div className="flex flex-col gap-1 items-start">
                          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[9px] font-bold bg-amber-500/15 text-amber-400 border border-amber-500/25 animate-pulse uppercase">
                            <RefreshCw size={8} className="animate-spin" />
                            {t('rebuildingLabel') || 'Rebuilding'}
                          </span>
                        </div>
                      ) : (
                        <span className="text-zinc-655">—</span>
                      )}
                    </td>

                    <td className="py-2 px-2.5 text-right whitespace-nowrap">
                      <div className="inline-flex items-center gap-1.5 justify-end">
                        {/* Toggle Active state (Block/Unblock) */}
                        {(kiosk.status === 'APPROVED' || kiosk.status === 'DISABLED' || kiosk.status === 'PENDING') && (
                          <button
                            onClick={() => handleToggleActive(kiosk.id)}
                            className={`px-2.5 py-1 border rounded text-[10px] font-bold transition-all cursor-pointer text-center whitespace-nowrap ${
                              kiosk.status === 'APPROVED'
                                ? 'bg-amber-955/20 border-amber-900/30 hover:border-amber-900/60 hover:bg-amber-955/40 text-amber-400'
                                : 'bg-emerald-955/20 border-emerald-900/30 hover:border-emerald-900/60 hover:bg-emerald-955/40 text-emerald-400'
                            }`}
                          >
                            {kiosk.status === 'APPROVED' ? t('kioskActionDisable') : t('kioskActionEnable')}
                          </button>
                        )}

                        {/* Download Kiosk ISO */}
                        {kiosk.iso_exists ? (
                          <a
                            href={`/api/iso/kiosks/${kiosk.id}/download`}
                            className="inline-block px-2.5 py-1 bg-indigo-955/20 border border-indigo-900/30 hover:border-indigo-900/60 hover:bg-indigo-955/40 text-indigo-400 rounded text-[10px] font-bold transition-all text-center whitespace-nowrap"
                          >
                            {t('kioskActionDownload')}
                          </a>
                        ) : (
                          <span className="px-2 py-1 bg-zinc-950/40 border border-zinc-800 text-zinc-550 italic text-[10px] rounded text-center whitespace-nowrap inline-block cursor-not-allowed select-none" title={t('issueKioskPrunedMsg')}>
                            {t('kioskStatusPruned') || 'Pruned'}
                          </span>
                        )}

                        {/* Re-create ISO */}
                        {kiosk.is_rebuilding ? (
                          <button
                            disabled
                            className="px-2 py-1 rounded text-[10px] font-bold bg-amber-500/20 text-amber-400 border border-amber-500/30 cursor-not-allowed text-center whitespace-nowrap inline-flex items-center justify-center gap-1"
                            title="Rebuilding ISO image..."
                          >
                            <RefreshCw size={10} className="animate-spin" />
                            {t('rebuildingLabel') || 'Rebuilding'}
                          </button>
                        ) : (
                          <button
                            onClick={() => handleRecreateIso(kiosk.id)}
                            className="px-2 py-1 rounded text-[10px] font-bold transition-all cursor-pointer text-center whitespace-nowrap inline-flex items-center justify-center gap-1 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 border border-zinc-700"
                            title="Recreate ISO image"
                          >
                            <RefreshCw size={10} />
                            {t('kioskActionRecreate') || 'Recreate'}
                          </button>
                        )}

                        {/* Edit Kiosk */}
                        <button
                          onClick={() => handleEditClick(kiosk)}
                          className="p-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 border border-zinc-700 hover:border-zinc-600 rounded transition-all cursor-pointer inline-flex items-center justify-center"
                          title={t('editLabel') || 'Edit'}
                        >
                          <Edit2 size={12} />
                        </button>

                        {/* Delete Kiosk */}
                        <button
                          onClick={() => handleDelete(kiosk.id)}
                          className="p-1.5 bg-zinc-800 hover:bg-rose-950/20 hover:bg-rose-900/30 hover:text-rose-400 text-zinc-400 border border-zinc-700 hover:border-rose-900/50 rounded transition-all cursor-pointer inline-flex items-center justify-center"
                          title={t('deleteLabel') || 'Delete'}
                        >
                          <Trash2 size={12} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Edit Modal */}
      {showEditModal && editingKiosk && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fade-in">
          <div className="w-full max-w-md p-6 bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl space-y-4 animate-modal-in">
            <div>
              <h3 className="text-base font-bold text-zinc-50">{t('editKioskTitle') || 'Edit Kiosk'}</h3>
              <p className="text-[10px] text-zinc-400 font-semibold uppercase tracking-wider">{t('editKioskSub') || 'Update friendly name, contact info, and comments.'}</p>
            </div>
            
            <form onSubmit={handleEditSubmit} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('kioskNameLabel') || 'Friendly Name'}</label>
                <input
                  type="text"
                  placeholder={t('kioskNamePlaceholder')}
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:border-indigo-500 focus:outline-none transition-colors"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('kioskContact') || 'Contact'}</label>
                <input
                  type="text"
                  placeholder={t('kioskContactPlaceholder') || 'e.g. @username or email'}
                  value={editContact}
                  onChange={(e) => setEditContact(e.target.value)}
                  className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:border-indigo-500 focus:outline-none transition-colors"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('kioskComment') || 'Comment'}</label>
                <textarea
                  rows={2}
                  placeholder={t('kioskCommentPlaceholder') || 'e.g. Backup kiosk for first floor'}
                  value={editComment}
                  onChange={(e) => setEditComment(e.target.value)}
                  className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:border-indigo-500 focus:outline-none transition-colors"
                />
              </div>

              {error && <div className="text-xs text-rose-400 bg-rose-500/10 border border-rose-500/20 p-3 rounded-lg">{error}</div>}

              <div className="flex justify-end gap-2 pt-2 border-t border-zinc-800">
                <button
                  type="button"
                  onClick={() => {
                    setShowEditModal(false);
                    setEditingKiosk(null);
                  }}
                  className="px-4 py-2 text-xs font-semibold text-zinc-400 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors cursor-pointer"
                >
                  {t('cancel') || 'Cancel'}
                </button>
                <button
                  type="submit"
                  disabled={submitting}
                  className="px-4 py-2 text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg disabled:opacity-50 transition-colors cursor-pointer"
                >
                  {submitting ? t('saving') : (t('saveLabel') || 'Save')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Update Target IP Modal */}
      {showIpModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fade-in">
          <div className="w-full max-w-sm p-6 bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl space-y-4 animate-modal-in">
            <div>
              <h3 className="text-base font-bold text-zinc-50">{t('updateTargetIpTitle') || 'Update Kiosk Target IP'}</h3>
              <p className="text-[10px] text-zinc-400 font-semibold uppercase tracking-wider">
                {t('updateTargetIpSub') || 'Specify custom orchestrator IP address for this client'}
              </p>
            </div>
            
            <form onSubmit={handleUpdateIpSubmit} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('targetIpLabel') || 'Target IP Address'}</label>
                <input
                  type="text"
                  required
                  placeholder="e.g. 192.168.1.100"
                  value={targetIpToUpdate}
                  onChange={(e) => setTargetIpToUpdate(e.target.value)}
                  className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:border-indigo-500 focus:outline-none transition-colors"
                />
              </div>

              {error && <div className="text-xs text-rose-400 bg-rose-500/10 border border-rose-500/20 p-3 rounded-lg">{error}</div>}

              <div className="flex justify-end gap-2 pt-2 border-t border-zinc-800">
                <button
                  type="button"
                  onClick={() => {
                    setShowIpModal(false);
                    setIpKioskId(null);
                  }}
                  className="px-4 py-2 text-xs font-semibold text-zinc-400 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors cursor-pointer"
                >
                  {t('cancel') || 'Cancel'}
                </button>
                <button
                  type="submit"
                  disabled={submitting}
                  className="px-4 py-2 text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg disabled:opacity-50 transition-colors cursor-pointer"
                >
                  {submitting ? t('saving') : (t('saveLabel') || 'Save')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
