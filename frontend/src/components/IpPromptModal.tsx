import React from 'react';
import { Settings as Gear } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';
import { DropdownTextInput } from './SearchableSelect';

interface IpPromptModalProps {
  onClose: () => void;
  onSubmit: (e: React.FormEvent) => void;
  orchestratorIp: string;
  setOrchestratorIp: (val: string) => void;
  availableIps: string[];
  savingIp: boolean;
}

export default function IpPromptModal({
  onClose,
  onSubmit,
  orchestratorIp,
  setOrchestratorIp,
  availableIps,
  savingIp
}: IpPromptModalProps) {
  const { t } = useTranslation();

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fade-in">
      <div className="w-full max-w-md p-6 bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl space-y-4 animate-modal-in">
        <div className="flex items-center gap-3 border-b border-zinc-800 pb-3">
          <div className="p-2 bg-amber-500/10 text-amber-400 border border-amber-500/20 rounded-lg">
            <Gear size={20} />
          </div>
          <div>
            <h3 className="text-base font-bold text-zinc-50 leading-tight">{t('welcomeSetup')}</h3>
            <p className="text-[10px] text-zinc-400 font-semibold uppercase tracking-wider">{t('configureOrchestratorIp')}</p>
          </div>
        </div>

        <div className="flex justify-center py-2 bg-zinc-950/60 rounded-xl border border-zinc-800/80">
          <img src="/edge_bro_logo.png" alt="Edge-B.R.O. Logo" className="w-40 h-40 object-contain rounded-lg shadow-lg border border-indigo-500/20" />
        </div>

        <p className="text-xs text-zinc-300 leading-relaxed font-medium">
          {t('welcomeExplanation')}
        </p>

        <form onSubmit={onSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('orchestratorIpLabel')}</label>
            <DropdownTextInput
              value={orchestratorIp}
              onChange={setOrchestratorIp}
              options={availableIps}
              required
              placeholder="e.g. 192.168.222.2 (IP accessible to edge nodes)"
            />
            <p className="text-[10px] text-zinc-500 mt-1">
              {t('orchestratorIpHint')}
            </p>
          </div>

          <div className="flex justify-end gap-2 pt-2 border-t border-zinc-800">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-xs font-semibold text-zinc-400 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors cursor-pointer"
            >
              {t('skip')}
            </button>
            <button
              type="submit"
              disabled={savingIp}
              className="px-4 py-2 text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg disabled:opacity-50 transition-colors cursor-pointer"
            >
              {savingIp ? t('saving') : t('saveAndContinue')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
