import React, { useState } from 'react';
import { Loader2, User } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';

interface ProfileModalProps {
  currentUser: any;
  onClose: () => void;
  onUpdateSuccess: (updatedUser: any) => void;
}

export default function ProfileModal({ currentUser, onClose, onUpdateSuccess }: ProfileModalProps) {
  const { t } = useTranslation();
  const [name, setName] = useState(currentUser.name || '');
  const [phone, setPhone] = useState(currentUser.phone || '');
  const [telegramId, setTelegramId] = useState(currentUser.telegram_id || '');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError('');

    const payload: any = {
      name: name.trim(),
      phone: phone.trim() || null,
      telegram_id: telegramId.trim() || null,
    };

    if (password) {
      if (password.length < 6) {
        setError('Password must be at least 6 characters long');
        setSubmitting(false);
        return;
      }
      payload.password = password;
    }

    try {
      const res = await fetch('/api/users/profile', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Failed to update profile');
      }

      onUpdateSuccess(data);
      onClose();
    } catch (err: any) {
      setError(err.message || 'An error occurred while updating profile');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fade-in">
      <div className="w-full max-w-md p-6 bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl space-y-4 animate-modal-in">
        <div className="flex items-center gap-3 border-b border-zinc-800 pb-3">
          <div className="p-2 bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 rounded-lg">
            <User size={20} />
          </div>
          <div>
            <h3 className="text-base font-bold text-zinc-50 leading-tight">{t('editProfile') || 'Edit Profile'}</h3>
            <p className="text-[10px] text-zinc-400 font-semibold uppercase tracking-wider font-mono">@{currentUser.username}</p>
          </div>
        </div>

        {error && (
          <div className="p-3 bg-rose-500/10 border border-rose-500/20 text-rose-400 rounded-xl text-xs font-semibold leading-relaxed">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
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
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 focus:border-indigo-500 rounded-lg text-zinc-100 text-sm focus:outline-none transition-all duration-200"
              placeholder={t('adminPasswordHint') || 'Leave blank to keep current password'}
            />
          </div>

          <div className="flex justify-end gap-2 pt-2 border-t border-zinc-800">
            <button
              type="button"
              onClick={onClose}
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
  );
}
