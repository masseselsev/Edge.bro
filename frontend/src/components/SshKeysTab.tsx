import { useEffect, useState } from 'react';
import { RefreshCw, Trash2, ShieldAlert } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';

interface Finding {
  id: number;
  location: string;
  host: string;
  fingerprint: string;
  comment: string | null;
  classification: string;
  reason: string | null;
  first_seen: string;
  pruned_at: string | null;
}

const CLASS_LABEL: Record<string, string> = {
  OURS_MATCHED: 'sshKeysClassMatched',
  OURS_ORPHANED: 'sshKeysClassOrphaned',
  OURS_LEGACY: 'sshKeysClassLegacy',
  UNKNOWN: 'sshKeysClassUnknown',
};

const CLASS_STYLE: Record<string, string> = {
  OURS_MATCHED: 'bg-emerald-950/50 text-emerald-400 border border-emerald-900/60',
  OURS_ORPHANED: 'bg-amber-950/50 text-amber-400 border border-amber-900/60',
  OURS_LEGACY: 'bg-zinc-800/60 text-zinc-400 border border-zinc-700/60',
  UNKNOWN: 'bg-red-950/50 text-red-400 border border-red-900/60',
};

export default function SshKeysTab() {
  const { t } = useTranslation();
  const [findings, setFindings] = useState<Finding[]>([]);
  const [orchestratorFp, setOrchestratorFp] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const [includeNodes, setIncludeNodes] = useState(false);
  const [showRemoved, setShowRemoved] = useState(false);
  const [abortReason, setAbortReason] = useState<string | null>(null);

  const load = async (withRemoved = showRemoved) => {
    const res = await fetch(`/api/ssh-keys/audit?include_resolved=${withRemoved}`);
    if (res.ok) setFindings(await res.json());
    const orch = await fetch('/api/ssh-keys/orchestrator');
    if (orch.ok) setOrchestratorFp((await orch.json()).fingerprint);
  };

  useEffect(() => { load(showRemoved); }, [showRemoved]);

  const runScan = async () => {
    setScanning(true);
    try {
      const res = await fetch(`/api/ssh-keys/audit/run?include_nodes=${includeNodes}`, {
        method: 'POST',
      });
      if (res.ok) {
        const summary = await res.json();
        setAbortReason(summary.aborted ? summary.abort_reason : null);
      }
      await load(showRemoved);
    } finally {
      setScanning(false);
    }
  };

  const purge = async (finding: Finding) => {
    const extra = finding.classification === 'UNKNOWN'
      ? `\n\n${t('sshKeysPurgeUnknownWarning')}`
      : '';
    if (!window.confirm(
      `${t('sshKeysPurgeConfirm')}${extra}\n\n${finding.fingerprint}\n${finding.comment ?? ''}`
    )) return;
    const res = await fetch(
      `/api/ssh-keys/findings/${finding.id}/purge?confirm=true`, { method: 'POST' }
    );
    if (!res.ok) {
      const body = await res.json().catch(() => ({ detail: 'Request failed' }));
      window.alert(body.detail);
    }
    await load(showRemoved);
  };

  return (
    <div className="space-y-5 animate-fade-in">
      <div>
        <h3 className="text-lg font-bold text-zinc-150">{t('sshKeysTitle')}</h3>
        <p className="text-sm text-zinc-450 mt-1">{t('sshKeysIntro')}</p>
        {orchestratorFp && (
          <p className="mt-2 font-mono text-xs text-zinc-500">
            {t('sshKeysOrchestratorKey')}: {orchestratorFp}
          </p>
        )}
      </div>

      {abortReason && (
        <div className="p-4 bg-amber-950/40 border border-amber-900/60 rounded-xl flex items-start gap-2">
          <ShieldAlert size={18} className="mt-0.5 shrink-0 text-amber-400" />
          <div className="text-xs text-amber-300">
            <span className="font-bold">{t('sshKeysAborted')}:</span> {abortReason}
          </div>
        </div>
      )}

      <div className="flex items-center gap-4">
        <button
          type="button"
          onClick={runScan}
          disabled={scanning}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-sm text-white transition-all cursor-pointer"
        >
          <RefreshCw size={16} className={scanning ? 'animate-spin' : ''} />
          {scanning ? t('sshKeysScanning') : t('sshKeysRunScan')}
        </button>
        <label className="flex items-center gap-2 text-sm text-zinc-450 cursor-pointer">
          <input
            type="checkbox"
            checked={includeNodes}
            onChange={(e) => setIncludeNodes(e.target.checked)}
          />
          {t('sshKeysIncludeNodes')}
        </label>
        <label className="flex items-center gap-2 text-sm text-zinc-450 cursor-pointer">
          <input
            type="checkbox"
            checked={showRemoved}
            onChange={(e) => setShowRemoved(e.target.checked)}
          />
          {t('sshKeysShowRemoved')}
        </label>
      </div>

      {findings.length === 0 ? (
        <p className="text-sm text-zinc-500">{t('sshKeysNoFindings')}</p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-zinc-800">
          <table className="min-w-full text-sm">
            <thead className="text-left text-xs uppercase text-zinc-500 bg-zinc-900/60">
              <tr>
                <th className="px-3 py-2 font-medium">{t('sshKeysColHost')}</th>
                <th className="px-3 py-2 font-medium">{t('sshKeysColFingerprint')}</th>
                <th className="px-3 py-2 font-medium">{t('sshKeysColComment')}</th>
                <th className="px-3 py-2 font-medium">{t('sshKeysColClass')}</th>
                <th className="px-3 py-2 font-medium">{t('sshKeysColFirstSeen')}</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {findings.map((f) => (
                <tr
                  key={f.id}
                  className={`border-t border-zinc-800 ${
                    f.pruned_at ? 'text-zinc-600 line-through' : 'text-zinc-300'
                  }`}
                >
                  <td className="px-3 py-2">
                    {f.host === '__orchestrator__' ? t('sshKeysLocationOrchestrator') : f.host}
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">{f.fingerprint}</td>
                  <td className="px-3 py-2 font-mono text-xs">{f.comment ?? '—'}</td>
                  <td className="px-3 py-2">
                    <span
                      className={`rounded px-2 py-0.5 text-xs ${CLASS_STYLE[f.classification] ?? ''}`}
                      title={f.reason ?? ''}
                    >
                      {t(CLASS_LABEL[f.classification] ?? f.classification)}
                    </span>
                    {f.pruned_at && (
                      <span className="ml-2 text-xs text-zinc-600">
                        {t('sshKeysPruned')} {new Date(f.pruned_at).toLocaleDateString()}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-xs">
                    {new Date(f.first_seen).toLocaleString()}
                  </td>
                  <td className="px-3 py-2 text-right">
                    {!f.pruned_at
                      && f.classification === 'UNKNOWN'
                      && f.location === 'ORCHESTRATOR' && (
                      <button
                        type="button"
                        onClick={() => purge(f)}
                        title={t('sshKeysPurge')}
                        className="text-red-500 hover:text-red-400 transition-all cursor-pointer"
                      >
                        <Trash2 size={16} />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
