import React, { useState, useEffect } from 'react';
import { Plus, Trash2, ShieldAlert, CheckCircle, RefreshCw, Clipboard, Copy, Server, Globe, Search } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';

interface Kiosk {
  id: number;
  name: string | null;
  uuid: string;
  key: string;
  status: 'PENDING' | 'APPROVED' | 'REVOKED';
  ip_address: string | null;
  ssh_pub_key: string | null;
  created_at: string;
  updated_at: string;
}

export default function KioskManagementSection() {
  const { t } = useTranslation();
  const [kiosks, setKiosks] = useState<Kiosk[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAddModal, setShowAddModal] = useState(false);
  const [showKeyModal, setShowKeyModal] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');

  const filteredKiosks = kiosks.filter(k => {
    const query = searchQuery.toLowerCase();
    const nameMatch = (k.name || '').toLowerCase().includes(query);
    const uuidMatch = (k.uuid || '').toLowerCase().includes(query);
    const ipMatch = (k.ip_address || '').toLowerCase().includes(query);
    
    // Check both raw status and translated status names if possible
    const statusMatch = (k.status || '').toLowerCase().includes(query);
    
    return nameMatch || uuidMatch || ipMatch || statusMatch;
  });
  
  // Form fields
  const [name, setName] = useState('');
  const [uuid, setUuid] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  
  // Key display modal state
  const [generatedKey, setGeneratedKey] = useState('');
  const [copied, setCopied] = useState(false);

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
    return () => clearInterval(interval);
  }, []);

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      const res = await fetch('/api/kiosks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name || null, uuid: uuid.trim() })
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Registration failed');
      }
      
      setGeneratedKey(data.key);
      setShowAddModal(false);
      setShowKeyModal(true);
      setName('');
      setUuid('');
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
          <button
            onClick={() => {
              setError('');
              setShowAddModal(true);
            }}
            className="flex items-center justify-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg font-bold text-xs shadow-lg transition-colors cursor-pointer shrink-0"
          >
            <Plus size={14} />
            {t('registerKioskButton') || 'Register Kiosk'}
          </button>
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
            <p className="text-[10px] text-zinc-500 mt-1">Try adjusting your search criteria</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left text-xs">
              <thead>
                <tr className="border-b border-zinc-800 text-zinc-400 font-semibold bg-zinc-950/40">
                  <th className="py-3 px-4">{t('kioskNameLabel') || 'Name'}</th>
                  <th className="py-3 px-4">{t('kioskUuidLabel') || 'UUID'}</th>
                  <th className="py-3 px-4">{t('keyLabel') || 'Pairing Key'}</th>
                  <th className="py-3 px-4">{t('statusLabel') || 'Status'}</th>
                  <th className="py-3 px-4">{t('ipAddressLabel') || 'IP Address'}</th>
                  <th className="py-3 px-4 text-right">{t('actionsLabel') || 'Actions'}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800/60">
                {filteredKiosks.map((kiosk) => (
                  <tr key={kiosk.id} className="hover:bg-zinc-950/20 transition-colors">
                    <td className="py-3.5 px-4 font-bold text-zinc-200">
                      {kiosk.name || <span className="text-zinc-500 italic">{t('unnamedKiosk') || 'Unnamed Kiosk'}</span>}
                    </td>
                    <td className="py-3.5 px-4 font-mono text-zinc-400 select-all">
                      {kiosk.uuid}
                    </td>
                    <td className="py-3.5 px-4 font-mono font-bold text-amber-400">
                      {kiosk.key}
                    </td>
                    <td className="py-3.5 px-4">
                      {kiosk.status === 'APPROVED' ? (
                        <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                          <CheckCircle size={10} /> {t('kioskApproved') || 'Authorized'}
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
                    </td>
                    <td className="py-3.5 px-4 text-zinc-300 font-mono">
                      {kiosk.ip_address || <span className="text-zinc-600">—</span>}
                    </td>
                    <td className="py-3.5 px-4 text-right space-x-2">
                      {kiosk.status === 'APPROVED' && (
                        <button
                          onClick={() => handleRevoke(kiosk.id)}
                          className="px-2 py-1 bg-red-950/20 border border-red-900/30 hover:border-red-900/60 hover:bg-red-950/40 text-red-400 rounded text-[10px] font-bold transition-all cursor-pointer"
                        >
                          {t('revokeAccess') || 'Revoke'}
                        </button>
                      )}
                      <button
                        onClick={() => handleDelete(kiosk.id)}
                        className="px-2 py-1 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded text-[10px] font-bold transition-all cursor-pointer"
                      >
                        {t('deleteLabel') || 'Delete'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Add Modal */}
      {showAddModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fade-in">
          <div className="w-full max-w-md p-6 bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl space-y-4 animate-modal-in">
            <div>
              <h3 className="text-base font-bold text-zinc-50">{t('registerKioskTitle') || 'Register Kiosk'}</h3>
              <p className="text-[10px] text-zinc-400 font-semibold uppercase tracking-wider">{t('registerKioskSub') || 'Generate security key for dynamic сопряжения'}</p>
            </div>
            
            <form onSubmit={handleRegister} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('kioskNameLabel') || 'Friendly Name'}</label>
                <input
                  type="text"
                  placeholder={t('kioskNamePlaceholder')}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:border-indigo-500 focus:outline-none transition-colors"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('kioskUuidLabel') || 'Kiosk UUID'}</label>
                <input
                  type="text"
                  required
                  placeholder={t('kioskUuidPlaceholder')}
                  value={uuid}
                  onChange={(e) => setUuid(e.target.value)}
                  className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:border-indigo-500 focus:outline-none transition-colors font-mono"
                />
                <p className="text-[10px] text-zinc-500 mt-1">{t('kioskIdHint') || 'Read this ID from the footer of the Kiosk client screen.'}</p>
              </div>

              {error && <div className="text-xs text-rose-400 bg-rose-500/10 border border-rose-500/20 p-3 rounded-lg">{error}</div>}

              <div className="flex justify-end gap-2 pt-2 border-t border-zinc-800">
                <button
                  type="button"
                  onClick={() => setShowAddModal(false)}
                  className="px-4 py-2 text-xs font-semibold text-zinc-400 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors cursor-pointer"
                >
                  {t('cancel') || 'Cancel'}
                </button>
                <button
                  type="submit"
                  disabled={submitting}
                  className="px-4 py-2 text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg disabled:opacity-50 transition-colors cursor-pointer"
                >
                  {submitting ? t('saving') : (t('registerKioskButton') || 'Register Kiosk')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Key Display Modal */}
      {showKeyModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fade-in">
          <div className="w-full max-w-md p-6 bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl space-y-4 animate-modal-in text-center">
            <div className="flex justify-center">
              <div className="p-3 bg-amber-500/10 text-amber-400 border border-amber-500/20 rounded-full">
                <Globe size={28} />
              </div>
            </div>
            <div>
              <h3 className="text-base font-bold text-zinc-50">{t('pairKeyGenerated') || 'Pairing Key Generated'}</h3>
              <p className="text-xs text-zinc-400 mt-1">{t('pairKeyGeneratedDesc') || 'Enter this key phrase on the kiosk client along with this orchestrator\'s IP address to complete pairing.'}</p>
            </div>

            <div className="flex items-center justify-between bg-zinc-950 p-4 border border-zinc-800 rounded-xl max-w-xs mx-auto">
              <span className="font-mono text-xl font-bold tracking-widest text-amber-400 select-all mx-auto">{generatedKey}</span>
              <button
                onClick={copyKeyToClipboard}
                className="p-2 bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 text-zinc-400 hover:text-zinc-200 rounded-lg transition-colors cursor-pointer"
                title={t('copyToClipboard') || 'Copy to clipboard'}
              >
                {copied ? <CheckCircle size={16} className="text-emerald-400" /> : <Copy size={16} />}
              </button>
            </div>

            <div className="pt-2 border-t border-zinc-800">
              <button
                onClick={() => {
                  setShowKeyModal(false);
                  setGeneratedKey('');
                }}
                className="px-5 py-2 text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors cursor-pointer"
              >
                {t('closeButton') || 'Close'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
