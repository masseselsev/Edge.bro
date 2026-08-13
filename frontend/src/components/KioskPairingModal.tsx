import React from 'react';
import { Link2, Copy } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';
import { DropdownTextInput } from './SearchableSelect';
import type { useKioskPairing } from '../hooks/useKioskPairing';

/**
 * The kiosk's "attach me to an orchestrator" dialog.
 *
 * Two tabs for the two ways in — request approval, or type a key you were
 * given — over one shared server-address field. All state and both submit
 * handlers live in `useKioskPairing`; this renders them.
 */

export interface KioskPairingModalProps {
  pairing: ReturnType<typeof useKioskPairing>;
}

export default function KioskPairingModal({ pairing }: KioskPairingModalProps) {
  const { t } = useTranslation();

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fade-in">
      <div className="w-full max-w-md p-6 bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl space-y-4 animate-modal-in">
        <div className="flex items-center gap-3 border-b border-zinc-800 pb-3">
          <div className="p-2 bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 rounded-lg">
            <Link2 size={20} className="animate-pulse" />
          </div>
          <div>
            <h3 className="text-base font-bold text-zinc-50 leading-tight">{t('linkOrchestratorTitle') || 'Connect to Orchestrator'}</h3>
            <p className="text-[10px] text-zinc-400 font-semibold uppercase tracking-wider">{t('linkOrchestratorSub') || 'Establish secure paired connection'}</p>
          </div>
        </div>

        {/* The administrator on the other end needs this id to match the
            request to the machine, and reading it aloud is error-prone. */}
        <div className="bg-zinc-950 border border-zinc-800/80 p-3 rounded-xl flex items-center justify-between">
          <div>
            <span className="text-[9px] text-zinc-500 font-bold uppercase block mb-0.5">{t('thisKioskId') || 'This Kiosk ID'}</span>
            <span className="font-mono text-xs text-zinc-300 font-semibold select-all">{pairing.kioskId || 'Generating...'}</span>
          </div>
          <button
            onClick={() => {
              navigator.clipboard.writeText(pairing.kioskId);
              alert(t('copied') || 'Copied!');
            }}
            className="p-2 bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 text-zinc-400 hover:text-zinc-200 rounded-lg transition-colors cursor-pointer"
            title={t('copyToClipboard') || 'Copy to Clipboard'}
          >
            <Copy size={14} />
          </button>
        </div>

        <div className="flex bg-zinc-950 rounded-lg p-1 gap-1 border border-zinc-800/40">
          <button
            type="button"
            onClick={() => pairing.selectMode('enroll')}
            className={`flex-1 py-1.5 text-[10px] font-bold rounded-md uppercase cursor-pointer transition-all ${
              pairing.mode === 'enroll'
                ? 'bg-indigo-600 text-white shadow-sm'
                : 'text-zinc-400 hover:text-zinc-50 hover:bg-zinc-900'
            }`}
          >
            {t('submitRequest') || 'Request Key'}
          </button>
          <button
            type="button"
            onClick={() => pairing.selectMode('connect')}
            className={`flex-1 py-1.5 text-[10px] font-bold rounded-md uppercase cursor-pointer transition-all ${
              pairing.mode === 'connect'
                ? 'bg-indigo-600 text-white shadow-sm'
                : 'text-zinc-400 hover:text-zinc-50 hover:bg-zinc-900'
            }`}
          >
            {t('enterPairingKey') || 'Enter Key'}
          </button>
        </div>

        {pairing.mode === 'enroll' ? (
          <form onSubmit={pairing.submitEnroll} className="space-y-4">
            {pairing.enrollMsg && (
              <div className="text-xs text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 p-3 rounded-lg leading-relaxed">
                {pairing.enrollMsg}
              </div>
            )}
            <div>
              <label className="block text-xs font-semibold text-zinc-400 mb-1.5">
                {t('selectServerIp') || 'Select Server IP'}
              </label>
              <DropdownTextInput
                value={pairing.ip}
                onChange={pairing.setIp}
                options={pairing.availableServerIps}
                required
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-zinc-400 mb-1.5">
                {t('kioskNameLabel') || 'Friendly Name'}
              </label>
              <input
                type="text"
                required
                placeholder={t('kioskNewNamePlaceholder') || 'e.g. Front desk kiosk'}
                value={pairing.name}
                onChange={(e) => pairing.setName(e.target.value)}
                className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:border-indigo-500 focus:outline-none transition-colors"
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-zinc-400 mb-1.5">
                {t('kioskPhone') || 'Phone'}
              </label>
              <input
                type="text"
                required
                placeholder={t('kioskPhonePlaceholder') || 'e.g. +1 555-0199'}
                value={pairing.phone}
                onChange={(e) => pairing.setPhone(e.target.value)}
                className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:border-indigo-500 focus:outline-none transition-colors"
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-zinc-400 mb-1.5">
                {t('kioskComment') || 'Comment'}
              </label>
              <textarea
                rows={2}
                required
                placeholder={t('kioskCommentPlaceholder') || 'e.g. Backup kiosk for first floor'}
                value={pairing.comment}
                onChange={(e) => pairing.setComment(e.target.value)}
                className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:border-indigo-500 focus:outline-none transition-colors"
              />
            </div>

            {pairing.error && <div className="text-xs text-rose-455 bg-rose-500/10 border border-rose-500/20 p-3 rounded-lg">{pairing.error}</div>}

            <div className="flex justify-end gap-2 pt-2 border-t border-zinc-800">
              <button
                type="button"
                onClick={pairing.closeModal}
                className="px-4 py-2 text-xs font-semibold text-zinc-400 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors cursor-pointer"
              >
                {t('cancel') || 'Cancel'}
              </button>
              <button
                type="submit"
                disabled={pairing.submitting}
                className="px-4 py-2 text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg disabled:opacity-50 transition-colors cursor-pointer"
              >
                {pairing.submitting ? t('saving') : (t('submitRequest') || 'Submit Request')}
              </button>
            </div>
          </form>
        ) : (
          <form onSubmit={pairing.submitConnect} className="space-y-4">
            {pairing.enrollMsg && (
              <div className="text-xs text-amber-400 bg-amber-500/10 border border-amber-500/20 p-3 rounded-lg leading-relaxed">
                {pairing.enrollMsg}
              </div>
            )}

            <div>
              <label className="block text-xs font-semibold text-zinc-400 mb-1.5">
                {t('selectServerIp') || 'Select Server IP'}
              </label>
              <DropdownTextInput
                value={pairing.ip}
                onChange={pairing.setIp}
                options={pairing.availableServerIps}
                required
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('pairKeyLabel') || 'Security Key (Format: 1234AB)'}</label>
              <input
                type="text"
                required
                placeholder="1234AB"
                value={pairing.key}
                onChange={(e) => pairing.setKey(e.target.value.toUpperCase())}
                className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-amber-400 font-bold text-sm tracking-widest focus:border-indigo-500 focus:outline-none transition-colors font-mono text-center placeholder:font-sans placeholder:tracking-normal"
              />
            </div>

            {pairing.error && <div className="text-xs text-rose-455 bg-rose-500/10 border border-rose-500/20 p-3 rounded-lg">{pairing.error}</div>}
            {pairing.success && <div className="text-xs text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 p-3 rounded-lg">{pairing.success}</div>}

            <div className="flex justify-end gap-2 pt-2 border-t border-zinc-800">
              <button
                type="button"
                onClick={pairing.closeModal}
                className="px-4 py-2 text-xs font-semibold text-zinc-400 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors cursor-pointer"
              >
                {t('cancel') || 'Cancel'}
              </button>
              <button
                type="submit"
                disabled={pairing.submitting}
                className="px-4 py-2 text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg disabled:opacity-50 transition-colors cursor-pointer"
              >
                {pairing.submitting ? t('saving') : (t('connectButton') || 'Connect')}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
