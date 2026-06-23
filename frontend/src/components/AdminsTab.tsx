import React, { useState, useEffect } from 'react';
import { Plus, Edit2, Trash2, Shield, Phone, MessageSquare, Loader2 } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';

interface User {
  id: number;
  username: string;
  name: string;
  phone: string | null;
  telegram_id: string | null;
  comment: string | null;
  is_superadmin: boolean;
  is_admin_plus: boolean;
}

interface AdminsTabProps {
  currentUser?: any;
}

export default function AdminsTab({ currentUser }: AdminsTabProps) {
  const { t } = useTranslation();
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingUser, setEditingUser] = useState<User | null>(null);

  // Form states
  const [username, setUsername] = useState('');
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [telegramId, setTelegramId] = useState('');
  const [password, setPassword] = useState('');
  const [isAdminPlus, setIsAdminPlus] = useState(false);
  const [comment, setComment] = useState('');
  const [formError, setFormError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const fetchUsers = async () => {
    try {
      const res = await fetch('/api/users');
      if (res.ok) {
        const data = await res.json();
        setUsers(data);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchUsers();
  }, []);

  const openCreateModal = () => {
    setEditingUser(null);
    setUsername('');
    setName('');
    setPhone('');
    setTelegramId('');
    setPassword('');
    setIsAdminPlus(false);
    setComment('');
    setFormError('');
    setModalOpen(true);
  };

  const openEditModal = (user: User) => {
    setEditingUser(user);
    setUsername(user.username);
    setName(user.name);
    setPhone(user.phone || '');
    setTelegramId(user.telegram_id || '');
    setPassword(''); // leave empty to not change
    setIsAdminPlus(user.is_admin_plus);
    setComment(user.comment || '');
    setFormError('');
    setModalOpen(true);
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setFormError('');

    const payload: any = {
      name,
      phone: phone.trim() || null,
      telegram_id: telegramId.trim() || null,
      comment: comment.trim() || null,
    };

    if (currentUser?.is_superadmin) {
      payload.is_admin_plus = isAdminPlus;
    }

    if (password) {
      if (password.length < 6) {
        setFormError('Password must be at least 6 characters long');
        setSubmitting(false);
        return;
      }
      payload.password = password;
    }

    try {
      let url = '/api/users';
      let method = 'POST';

      if (editingUser) {
        url = `/api/users/${editingUser.id}`;
        method = 'PUT';
      } else {
        // Create requires username and password
        payload.username = username.trim();
        if (!password) {
          setFormError('Password is required for new administrator accounts');
          setSubmitting(false);
          return;
        }
        payload.password = password;
      }

      const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.detail || 'Failed to save administrator');
      }

      await fetchUsers();
      setModalOpen(false);
    } catch (err: any) {
      setFormError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (user: User) => {
    if (!window.confirm(t('deleteAdminConfirm') || `Are you sure you want to delete admin ${user.username}?`)) {
      return;
    }

    try {
      const res = await fetch(`/api/users/${user.id}`, { method: 'DELETE' });
      if (res.ok) {
        await fetchUsers();
      } else {
        const errData = await res.json();
        alert(errData.detail || 'Failed to delete administrator');
      }
    } catch (err) {
      console.error(err);
    }
  };

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-zinc-500 gap-2">
        <Loader2 className="animate-spin text-indigo-500" size={24} />
        <span className="text-sm font-semibold">{t('loading') || 'Loading...'}</span>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header and Add Button */}
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-zinc-50 flex items-center gap-2">
            <Shield size={22} className="text-indigo-400" />
            {t('tabAdmins')}
          </h2>
          <p className="text-xs text-zinc-400 mt-1">Manage platform administrators, privileges, and comments.</p>
        </div>
        <button
          onClick={openCreateModal}
          className="flex items-center gap-1.5 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg font-bold text-xs tracking-wide shadow transition-colors cursor-pointer"
        >
          <Plus size={14} /> {t('createAdmin')}
        </button>
      </div>

      {/* Admins Table */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-2xl shadow-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="border-b border-zinc-800 bg-zinc-950/50 text-zinc-400 font-bold">
                <th className="p-4">{t('loginUsername')}</th>
                <th className="p-4">{t('adminName')}</th>
                <th className="p-4">{t('adminPhone')}</th>
                <th className="p-4">{t('adminTelegram')}</th>
                <th className="p-4">{t('adminComment')}</th>
                <th className="p-4 text-right">{t('actions')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-850">
              {users.map((user) => (
                <tr key={user.id} className="hover:bg-zinc-850/30 text-zinc-300 transition-colors">
                  <td className="p-4 font-mono font-bold text-zinc-100 flex items-center gap-1.5">
                    {user.username}
                    {user.is_superadmin && (
                      <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
                        SUPER
                      </span>
                    )}
                    {user.is_admin_plus && (
                      <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-amber-500/10 text-amber-400 border border-amber-500/20 animate-fade-in">
                        ADMIN+
                      </span>
                    )}
                  </td>
                  <td className="p-4 font-semibold">{user.name}</td>
                  <td className="p-4 font-mono text-zinc-400">
                    {user.phone ? (
                      <span className="flex items-center gap-1">
                        <Phone size={12} className="text-zinc-500" />
                        {user.phone}
                      </span>
                    ) : (
                      <span className="text-zinc-650">—</span>
                    )}
                  </td>
                  <td className="p-4 font-mono text-zinc-400">
                    {user.telegram_id ? (
                      <span className="flex items-center gap-1">
                        <MessageSquare size={12} className="text-zinc-500" />
                        @{user.telegram_id}
                      </span>
                    ) : (
                      <span className="text-zinc-650">—</span>
                    )}
                  </td>
                  <td className="p-4 text-zinc-400 max-w-xs truncate" title={user.comment || ''}>
                    {user.comment || <span className="text-zinc-650">—</span>}
                  </td>
                  <td className="p-4 text-right space-x-1.5 whitespace-nowrap">
                    {(currentUser?.is_superadmin || (!user.is_superadmin && !user.is_admin_plus)) && (
                      <button
                        onClick={() => openEditModal(user)}
                        className="p-1.5 bg-zinc-950 border border-zinc-800 hover:border-zinc-700 text-zinc-400 hover:text-zinc-200 rounded-lg transition-all cursor-pointer"
                        title={t('editAdmin')}
                      >
                        <Edit2 size={13} />
                      </button>
                    )}
                    {!user.is_superadmin && (currentUser?.is_superadmin || !user.is_admin_plus) && (
                      <button
                        onClick={() => handleDelete(user)}
                        className="p-1.5 bg-rose-950/20 border border-rose-900/30 hover:border-rose-900/60 text-rose-450 hover:text-rose-400 rounded-lg transition-all cursor-pointer"
                        title={t('deleteLabel') || 'Delete'}
                      >
                        <Trash2 size={13} />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Create / Edit Modal */}
      {modalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fade-in">
          <div className="w-full max-w-md p-6 bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl space-y-4 animate-modal-in">
            <h3 className="text-base font-bold text-zinc-50 border-b border-zinc-800 pb-3">
              {editingUser ? t('editAdmin') : t('createAdmin')}
            </h3>

            {formError && (
              <div className="p-3 bg-rose-500/10 border border-rose-500/20 text-rose-400 rounded-xl text-xs font-semibold leading-relaxed">
                {formError}
              </div>
            )}

            <form onSubmit={handleSave} className="space-y-3.5">
              {!editingUser && (
                <div className="space-y-1">
                  <label className="block text-xs font-bold text-zinc-400 uppercase tracking-wider pl-1">
                    {t('loginUsername')}
                  </label>
                  <input
                    type="text"
                    required
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 focus:border-indigo-500 rounded-lg text-zinc-100 text-sm focus:outline-none transition-all duration-200 font-mono"
                    placeholder="e.g. admin_technician"
                  />
                </div>
              )}

              <div className="space-y-1">
                <label className="block text-xs font-bold text-zinc-400 uppercase tracking-wider pl-1">
                  {t('adminName')}
                </label>
                <input
                  type="text"
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 focus:border-indigo-500 rounded-lg text-zinc-100 text-sm focus:outline-none transition-all duration-200"
                  placeholder="e.g. John Doe"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <label className="block text-xs font-bold text-zinc-400 uppercase tracking-wider pl-1">
                    {t('adminPhone')}
                  </label>
                  <input
                    type="text"
                    value={phone}
                    onChange={(e) => setPhone(e.target.value)}
                    className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 focus:border-indigo-500 rounded-lg text-zinc-100 text-sm focus:outline-none transition-all duration-200 font-mono"
                    placeholder="e.g. +79991234567"
                  />
                </div>
                <div className="space-y-1">
                  <label className="block text-xs font-bold text-zinc-400 uppercase tracking-wider pl-1">
                    {t('adminTelegram')}
                  </label>
                  <input
                    type="text"
                    value={telegramId}
                    onChange={(e) => setTelegramId(e.target.value)}
                    className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 focus:border-indigo-500 rounded-lg text-zinc-100 text-sm focus:outline-none transition-all duration-200 font-mono"
                    placeholder="e.g. username"
                  />
                </div>
              </div>

              <div className="space-y-1">
                <label className="block text-xs font-bold text-zinc-400 uppercase tracking-wider pl-1">
                  {t('loginPassword')}
                </label>
                <input
                  type="password"
                  required={!editingUser}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 focus:border-indigo-500 rounded-lg text-zinc-100 text-sm focus:outline-none transition-all duration-200"
                  placeholder={editingUser ? t('adminPasswordHint') : '••••••••'}
                />
              </div>

              {currentUser?.is_superadmin && (
                <div className="flex items-center gap-2 py-1.5 pl-1">
                  <input
                    type="checkbox"
                    id="is_admin_plus"
                    checked={isAdminPlus}
                    onChange={(e) => setIsAdminPlus(e.target.checked)}
                    disabled={editingUser?.is_superadmin}
                    className="rounded border-zinc-800 bg-zinc-950 text-indigo-600 focus:ring-indigo-500 h-4 w-4 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                  />
                  <label htmlFor="is_admin_plus" className="text-xs font-bold text-zinc-300 cursor-pointer select-none flex flex-col pl-1">
                    <span>{t('adminPlusStatus')}</span>
                    <span className="text-[10px] text-zinc-500 font-semibold normal-case leading-normal mt-0.5">{t('adminPlusStatusDesc')}</span>
                  </label>
                </div>
              )}

              <div className="space-y-1">
                <label className="block text-xs font-bold text-zinc-400 uppercase tracking-wider pl-1">
                  {t('adminComment')}
                </label>
                <textarea
                  rows={2}
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                  className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 focus:border-indigo-500 rounded-lg text-zinc-100 text-sm focus:outline-none transition-all duration-200"
                  placeholder="Notes about this administrator account..."
                />
              </div>

              <div className="flex justify-end gap-2 pt-2 border-t border-zinc-800">
                <button
                  type="button"
                  onClick={() => setModalOpen(false)}
                  className="px-4 py-2 text-xs font-bold text-zinc-400 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors cursor-pointer"
                >
                  {t('cancel')}
                </button>
                <button
                  type="submit"
                  disabled={submitting}
                  className="px-4 py-2 text-xs font-bold text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg disabled:opacity-50 transition-colors flex items-center gap-1.5 cursor-pointer"
                >
                  {submitting && <Loader2 size={12} className="animate-spin" />}
                  {t('saveChanges')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
