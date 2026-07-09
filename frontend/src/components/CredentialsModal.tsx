import React, { useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from '../context/TranslationContext';
import { Trash2, Plus, Key } from 'lucide-react';

interface Credential {
  id: string;
  username: string;
  password: string;
}

interface CredentialsModalProps {
  onClose: () => void;
  credentials: Credential[];
  defaultId: string;
  onChange: (creds: Credential[], defaultId: string) => void;
}

export function CredentialsModal({ onClose, credentials, defaultId, onChange }: CredentialsModalProps) {
  const { t } = useTranslation();
  const [usernameInput, setUsernameInput] = useState('');
  const [passwordInput, setPasswordInput] = useState('');
  const [error, setError] = useState('');

  const handleAdd = (e: React.FormEvent) => {
    e.preventDefault();
    const username = usernameInput.trim();
    const password = passwordInput.trim();

    if (!username || !password) {
      setError(t('fillAllFields') || 'Please fill in both fields');
      return;
    }

    const newCred: Credential = {
      id: `cred_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
      username,
      password,
    };

    const updatedCreds = [...credentials, newCred];
    // If it's the first credential, make it default automatically
    const newDefaultId = credentials.length === 0 ? newCred.id : defaultId;

    onChange(updatedCreds, newDefaultId);
    setUsernameInput('');
    setPasswordInput('');
    setError('');
  };

  const handleDelete = (id: string) => {
    const updatedCreds = credentials.filter(c => c.id !== id);
    let newDefaultId = defaultId;
    if (defaultId === id) {
      newDefaultId = updatedCreds.length > 0 ? updatedCreds[0].id : '';
    }
    onChange(updatedCreds, newDefaultId);
  };

  const handleSetDefault = (id: string) => {
    onChange(credentials, id);
  };

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-fade-in">
      <div className="w-full max-w-md p-6 bg-zinc-900 border border-zinc-800 rounded-2xl shadow-xl space-y-4 animate-modal-in">
        <div className="flex items-center gap-2 border-b border-zinc-850 pb-3">
          <Key className="text-indigo-400" size={18} />
          <h3 className="text-lg font-bold text-zinc-50">{t('credentialsModalTitle')}</h3>
        </div>

        <div className="space-y-2 max-h-60 overflow-y-auto pr-1">
          {credentials.length === 0 ? (
            <p className="text-xs text-zinc-500 text-center py-4">{t('noCredentialsAdded')}</p>
          ) : (
            credentials.map((cred) => {
              const isDefault = defaultId === cred.id;
              return (
                <div key={cred.id} className="flex items-center justify-between p-2.5 bg-zinc-950 border border-zinc-800/80 rounded-lg hover:border-zinc-700/65 transition-all">
                  <div className="flex items-center gap-3">
                    <input
                      type="radio"
                      name="default_cred"
                      checked={isDefault}
                      onChange={() => handleSetDefault(cred.id)}
                      className="rounded-full border-zinc-700 bg-zinc-950 text-indigo-600 focus:ring-indigo-500 cursor-pointer h-4 w-4"
                    />
                    <div className="flex flex-col">
                      <span className="text-sm font-semibold text-zinc-250 font-mono">{cred.username}</span>
                      <span className="text-[10px] text-zinc-500 font-mono">••••••••</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    {isDefault && (
                      <span className="px-1.5 py-0.5 rounded text-[8px] font-bold bg-indigo-600/20 text-indigo-300 border border-indigo-500/30 uppercase tracking-wide">
                        {t('defaultCredentialsLabel')}
                      </span>
                    )}
                    <button
                      type="button"
                      onClick={() => handleDelete(cred.id)}
                      className="p-1 hover:bg-rose-500/15 text-rose-400 rounded-md transition-colors cursor-pointer"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>
              );
            })
          )}
        </div>

        <form onSubmit={handleAdd} className="border-t border-zinc-850 pt-4 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-[10px] font-semibold text-zinc-400 uppercase tracking-wider mb-1 pl-0.5">{t('usernameLabel')}</label>
              <input
                type="text"
                required
                placeholder="e.g. root"
                value={usernameInput}
                onChange={(e) => setUsernameInput(e.target.value)}
                className="w-full px-3 py-1.5 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-150 text-xs focus:border-indigo-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-[10px] font-semibold text-zinc-400 uppercase tracking-wider mb-1 pl-0.5">{t('passwordLabel')}</label>
              <input
                type="password"
                required
                placeholder="e.g. admin"
                value={passwordInput}
                onChange={(e) => setPasswordInput(e.target.value)}
                className="w-full px-3 py-1.5 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-150 text-xs focus:border-indigo-500 focus:outline-none"
              />
            </div>
          </div>

          {error && <p className="text-[10px] text-rose-400">{error}</p>}

          <button
            type="submit"
            className="w-full py-2 bg-zinc-850 hover:bg-zinc-800 text-zinc-100 rounded-lg font-bold text-xs transition-colors flex items-center justify-center gap-1.5 cursor-pointer"
          >
            <Plus size={13} />
            {t('addCredentialBtn')}
          </button>
        </form>

        <div className="flex justify-end pt-2 border-t border-zinc-850">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-xs font-bold text-zinc-350 bg-zinc-800 hover:bg-zinc-750 rounded-lg transition-colors cursor-pointer"
          >
            {t('cancel')}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
