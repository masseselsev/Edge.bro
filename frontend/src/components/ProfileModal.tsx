import React, { useState, useEffect } from 'react';
import { Loader2, User, Send, Check, X } from 'lucide-react';
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
  const [telegramConfigured, setTelegramConfigured] = useState(false);
  const [telegramEnabled, setTelegramEnabled] = useState(false);
  const [minSeverity, setMinSeverity] = useState<'WATCH' | 'ALERT'>('WATCH');
  const [prefsLoaded, setPrefsLoaded] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; detail: string } | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const [statusRes, prefsRes] = await Promise.all([
          fetch('/api/notifications/status'),
          fetch('/api/notifications/preferences'),
        ]);
        if (statusRes.ok) {
          setTelegramConfigured((await statusRes.json()).telegram_configured);
        }
        if (prefsRes.ok) {
          const prefs = await prefsRes.json();
          setTelegramEnabled(prefs.telegram_enabled);
          setMinSeverity(prefs.min_severity);
          setPrefsLoaded(true);
        }
      } catch (err) {
        console.error('Failed to load notification preferences:', err);
      }
    })();
  }, []);

  const handleTestNotification = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await fetch('/api/notifications/test', { method: 'POST' });
      const data = await res.json();
      setTestResult(data);
    } catch (err: any) {
      setTestResult({ success: false, detail: err.message || t('notificationsTestFailed') });
    } finally {
      setTesting(false);
    }
  };

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

      if (prefsLoaded) {
        try {
          const prefsRes = await fetch('/api/notifications/preferences', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ telegram_enabled: telegramEnabled, min_severity: minSeverity }),
          });
          if (!prefsRes.ok) {
            const prefsData = await prefsRes.json().catch(() => ({}));
            setError(prefsData.detail || 'Profile saved, but notification preferences failed to save');
            setSubmitting(false);
            return;
          }
        } catch (err) {
          console.error('Failed to save notification preferences:', err);
          setError('Profile saved, but notification preferences failed to save');
          setSubmitting(false);
          return;
        }
      }

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
                placeholder={t('adminTelegramPlaceholder')}
              />
              <p className="text-[10px] text-zinc-500 pl-1 leading-snug">{t('adminTelegramHint')}</p>
            </div>
          </div>

          <div className="space-y-2 pt-1 border-t border-zinc-800">
            <label className="block text-xs font-bold text-zinc-400 uppercase tracking-wider pl-1 pt-3">
              {t('notificationsSectionTitle')}
            </label>

            {!telegramConfigured ? (
              <p className="text-xs text-zinc-500 leading-relaxed px-1">
                {t('notificationsNotConfigured')}
              </p>
            ) : (
              <>
                <label className="flex items-center gap-2 px-1 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={telegramEnabled}
                    onChange={(e) => setTelegramEnabled(e.target.checked)}
                    className="w-4 h-4 rounded border-zinc-700 bg-zinc-950 text-indigo-600 focus:ring-indigo-500 cursor-pointer"
                  />
                  <span className="text-xs font-semibold text-zinc-300">
                    {t('notificationsEnableTelegram')}
                  </span>
                </label>

                {telegramEnabled && !telegramId.trim() && (
                  <p className="text-[11px] text-amber-400 px-1 leading-snug">
                    {t('notificationsNoTelegramIdWarning')}
                  </p>
                )}

                {telegramEnabled && (
                  <div className="flex items-center gap-3 px-1">
                    <label className="text-xs text-zinc-400 font-semibold">
                      {t('notificationsMinSeverity')}
                    </label>
                    <select
                      value={minSeverity}
                      onChange={(e) => setMinSeverity(e.target.value as 'WATCH' | 'ALERT')}
                      className="px-2 py-1 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-xs focus:outline-none focus:border-indigo-500"
                    >
                      <option value="WATCH">{t('healthStatusWatch') || 'WATCH'}</option>
                      <option value="ALERT">{t('healthStatusAlert') || 'ALERT'}</option>
                    </select>

                    <button
                      type="button"
                      onClick={handleTestNotification}
                      disabled={testing || !telegramId.trim() || telegramId.trim() !== (currentUser.telegram_id || '')}
                      className="ml-auto flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-bold text-zinc-300 bg-zinc-800 hover:bg-zinc-700 rounded-lg disabled:opacity-40 transition-colors cursor-pointer"
                    >
                      {testing ? <Loader2 size={11} className="animate-spin" /> : <Send size={11} />}
                      {t('notificationsSendTest')}
                    </button>
                  </div>
                )}

                {telegramEnabled && telegramId.trim() && telegramId.trim() !== (currentUser.telegram_id || '') && (
                  <p className="text-[11px] text-zinc-500 px-1 leading-snug">
                    {t('notificationsTestNeedsSave')}
                  </p>
                )}

                {testResult && (
                  <p className={`text-[11px] px-1 flex items-center gap-1 ${testResult.success ? 'text-emerald-400' : 'text-rose-400'}`}>
                    {testResult.success ? <Check size={11} /> : <X size={11} />}
                    {testResult.detail}
                  </p>
                )}
              </>
            )}
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
