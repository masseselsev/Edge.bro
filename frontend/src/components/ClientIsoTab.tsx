import React, { useState, useEffect } from 'react';
import { Download, Cpu, RefreshCw, CheckCircle, ShieldAlert, History, Trash2 } from 'lucide-react';
import { DropdownTextInput } from './SearchableSelect';
import { useTranslation } from '../context/TranslationContext';
import KioskManagementSection from './KioskManagementSection';

interface IsoStatus {
  base_iso_cached: boolean;
  client_iso_ready: boolean;
  base_iso_progress?: number;
  base_iso_speed?: string;
  iso_cache_free_space?: number;
  iso_cache_total_space?: number;
}

const formatBytes = (bytes: number) => {
  if (!bytes) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
};

interface ClientIsoTabProps {
  onViewLogs: (taskId: string, title: string) => void;
}

export default function ClientIsoTab({ onViewLogs }: ClientIsoTabProps) {
  const { t, language } = useTranslation();
  const [status, setStatus] = useState<IsoStatus | null>(null);
  const [orchestratorIp, setOrchestratorIp] = useState(window.location.hostname);
  const [availableIps, setAvailableIps] = useState<string[]>([]);
  const [isGenerating, setIsGenerating] = useState(false);
  const [isDownloadingBase, setIsDownloadingBase] = useState(false);
  const [error, setError] = useState('');
  const [successMsg, setSuccessMsg] = useState('');

  // Issue Kiosk Modal States
  const [showIssueModal, setShowIssueModal] = useState(false);
  const [issueName, setIssueName] = useState('');
  const [issueContact, setIssueContact] = useState('');
  const [issueComment, setIssueComment] = useState('');
  const [isIssuing, setIsIssuing] = useState(false);

  // Kiosk ISO History Modal States
  const [showHistoryModal, setShowHistoryModal] = useState(false);
  const [kiosks, setKiosks] = useState<any[]>([]);

  const fetchKiosks = async () => {
    try {
      fetchStatus();
      const res = await fetch('/api/kiosks');
      if (res.ok) {
        const data = await res.json();
        setKiosks(data);
      }
    } catch (err) {
      console.error('Failed to fetch kiosks:', err);
    }
  };

  const handleDeleteKiosk = async (id: number) => {
    if (window.confirm(t('deleteConfirm') || 'Are you sure you want to delete this kiosk and its ISO file?')) {
      try {
        const res = await fetch(`/api/kiosks/${id}`, { method: 'DELETE' });
        if (res.ok) {
          fetchKiosks();
          window.dispatchEvent(new CustomEvent('kiosks-updated'));
        }
      } catch (err) {
        console.error('Failed to delete kiosk:', err);
      }
    }
  };

  // Custom ISO Source States
  const [isoSourceType, setIsoSourceType] = useState<'official' | 'url' | 'upload'>('official');
  const [customIsoUrl, setCustomIsoUrl] = useState('');
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [lastTriggerTime, setLastTriggerTime] = useState<number>(0);
  const fileInputRef = React.useRef<HTMLInputElement>(null);

  const fetchStatus = async () => {
    try {
      const res = await fetch('/api/iso/status');
      const data = await res.json();
      setStatus(data);
      
      const timeSinceTrigger = Date.now() - lastTriggerTime;
      // Only reset isDownloadingBase if the backend says it is not downloading
      // AND we did not just trigger a download in the last 6 seconds (avoids state flicker before task starts on backend)
      if (timeSinceTrigger > 6000) {
        if (data && (data.base_iso_cached || data.base_iso_progress < 0)) {
          setIsDownloadingBase(false);
        }
      }
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 5000);
    return () => clearInterval(interval);
  }, [lastTriggerTime]);

  useEffect(() => {
    fetch('/api/settings')
      .then(res => res.json())
      .then(data => {
        if (data) {
          const autoIps = data.available_ips || [];
          const customIps = data.server_ips || [];
          const combined = Array.from(new Set([...autoIps, ...customIps]));
          setAvailableIps(combined);
          if (data.orchestrator_ip) {
            setOrchestratorIp(data.orchestrator_ip);
          }
        }
      })
      .catch(err => console.error(err));
  }, []);

  const handleGenerate = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsGenerating(true);
    setError('');
    setSuccessMsg('');
    try {
      const res = await fetch('/api/iso/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target_ip: orchestratorIp,
          auth_token: 'TEMPLATE'
        })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to start generation');
      
      if (data.task_id) {
        onViewLogs(data.task_id, t('taskLogsModalTitle') || 'Live-USB Generation Progress');
      } else {
        setSuccessMsg('ISO Generation task started in background.');
      }
      
      // Start polling faster
      setTimeout(fetchStatus, 3000);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setIsGenerating(false);
    }
  };

  const handleIssueSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsIssuing(true);
    setError('');
    try {
      const res = await fetch('/api/iso/kiosks/issue', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: issueName.trim(),
          contact: issueContact.trim(),
          comment: issueComment.trim() || null
        })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to issue kiosk');

      setShowIssueModal(false);
      setIssueName('');
      setIssueContact('');
      setIssueComment('');

      if (data.task_id) {
        onViewLogs(data.task_id, t('issueKioskGenerating') || 'Repackaging ISO image...');
      }
      
      fetchStatus();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setIsIssuing(false);
    }
  };

  const handleCacheBaseIso = async () => {
    if (isDownloadingBase || (status?.base_iso_progress !== undefined && status.base_iso_progress >= 0)) {
      return;
    }
    setLastTriggerTime(Date.now());
    setIsDownloadingBase(true);
    try {
      const body = isoSourceType === 'url' ? JSON.stringify({ url: customIsoUrl }) : JSON.stringify({});
      const res = await fetch('/api/iso/download_base', { 
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to start base ISO download');
      setSuccessMsg('Base ISO download started in the background. It may take several minutes depending on network speed.');
      // Force status poll
      fetchStatus();
    } catch (e: any) {
      setIsDownloadingBase(false);
      setError(e.message);
    }
  };

  const handleClearCache = async () => {
    try {
      await fetch('/api/iso/base', { method: 'DELETE' });
      setSuccessMsg('Base ISO cache cleared.');
      fetchStatus();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleUpload = () => {
    if (!fileInputRef.current?.files?.[0]) return;
    const file = fileInputRef.current.files[0];
    if (!file.name.endsWith('.iso')) {
      setError('Please select a valid .iso file');
      return;
    }

    setIsUploading(true);
    setUploadProgress(0);
    setError('');
    
    const formData = new FormData();
    formData.append('file', file);

    const xhr = new XMLHttpRequest();
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        setUploadProgress(Math.round((e.loaded / e.total) * 100));
      }
    };
    
    xhr.onload = () => {
      setIsUploading(false);
      if (xhr.status === 200) {
        setSuccessMsg('Custom Base ISO uploaded successfully!');
        fetchStatus();
      } else {
        try {
          const res = JSON.parse(xhr.responseText);
          setError(res.detail || 'Upload failed');
        } catch {
          setError('Upload failed');
        }
      }
    };
    
    xhr.onerror = () => {
      setIsUploading(false);
      setError('Network error during upload');
    };

    xhr.open('POST', '/api/iso/upload_base');
    xhr.send(formData);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3 mb-8">
        <div className="p-3 bg-indigo-500/10 border border-indigo-500/20 rounded-xl">
          <Cpu className="text-indigo-400" size={24} />
        </div>
        <div>
          <h2 className="text-xl font-bold text-zinc-50 tracking-tight">{t('tabLiveCdKiosks') || 'Live-CD & Kiosks'}</h2>
          <p className="text-xs text-zinc-400 mt-1">{t('tabLiveCdKiosksSub') || 'Compile Live-CD ISO images and manage paired Kiosk terminals.'}</p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-10 gap-6 animate-fade-in">
        {/* Left Column (30% / 3 cols) */}
        <div className="lg:col-span-3 space-y-6">
          {/* Base ISO Cache Panel (Prerequisite Panel) */}
          <div className="p-5 bg-zinc-900 border border-zinc-800 rounded-2xl shadow-xl space-y-4">
            <h3 className="text-sm font-bold text-zinc-50">{t('pipelineStatus') || 'Pipeline Status'}</h3>
            
            <div className="p-3 bg-zinc-950 border border-zinc-800/80 rounded-xl">
              <div className="flex items-center justify-between mb-3">
                <div>
                  <div className="text-xs font-bold text-zinc-50">{t('baseIsoCache') || 'Base Debian ISO Cache'}</div>
                  <div className="text-[10px] text-zinc-500">
                    {status?.base_iso_cached ? (t('isoReady') || 'ISO image is ready') : (t('selectIsoSource') || 'Select base ISO source')}
                  </div>
                </div>
                {status?.base_iso_cached && (
                  <div className="flex items-center gap-2">
                    <CheckCircle className="text-emerald-400" size={20} />
                    <button 
                      onClick={handleClearCache}
                      className="p-1 hover:bg-rose-500/20 text-rose-400 rounded transition-colors cursor-pointer"
                      title={t('clearCachedIso') || 'Clear Cached ISO'}
                    >
                      <RefreshCw size={16} />
                    </button>
                  </div>
                )}
              </div>

              {!status?.base_iso_cached && (
                <div className="space-y-4">
                  <div className="flex bg-zinc-900 rounded-lg p-1 gap-1">
                    <button
                      type="button"
                      onClick={() => setIsoSourceType('official')}
                      className={`flex-1 py-1.5 text-[10px] font-bold rounded-md uppercase cursor-pointer transition-colors ${isoSourceType === 'official' ? 'bg-indigo-600 text-white' : 'text-zinc-400 hover:text-zinc-50 hover:bg-zinc-800'}`}
                    >
                      {t('officialTab') || 'Official'}
                    </button>
                    <button
                      type="button"
                      onClick={() => setIsoSourceType('url')}
                      className={`flex-1 py-1.5 text-[10px] font-bold rounded-md uppercase cursor-pointer transition-colors ${isoSourceType === 'url' ? 'bg-indigo-600 text-white' : 'text-zinc-400 hover:text-zinc-50 hover:bg-zinc-800'}`}
                    >
                      {t('customUrlTab') || 'Custom URL'}
                    </button>
                    <button
                      type="button"
                      onClick={() => setIsoSourceType('upload')}
                      className={`flex-1 py-1.5 text-[10px] font-bold rounded-md uppercase cursor-pointer transition-colors ${isoSourceType === 'upload' ? 'bg-indigo-600 text-white' : 'text-zinc-400 hover:text-zinc-50 hover:bg-zinc-800'}`}
                    >
                      {t('uploadTab') || 'Upload'}
                    </button>
                  </div>

                  {isoSourceType === 'official' && (
                    <div className="flex flex-col gap-2">
                      <div className="text-[10px] text-zinc-400 font-mono">debian-live-testing-amd64-xfce.iso (4GB)</div>
                      <button
                        onClick={handleCacheBaseIso}
                        disabled={isDownloadingBase || (status?.base_iso_progress !== undefined && status.base_iso_progress >= 0)}
                        className="w-full py-2 text-xs font-bold bg-zinc-800 hover:bg-zinc-700 text-zinc-100 rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
                      >
                        {isDownloadingBase || (status?.base_iso_progress !== undefined && status.base_iso_progress >= 0) ? (t('downloadProgress') || 'Downloading...') : (t('startDownload') || 'START DOWNLOAD')}
                      </button>
                    </div>
                  )}

                  {isoSourceType === 'url' && (
                    <div className="flex flex-col gap-2">
                      <input 
                        type="url" 
                        placeholder="https://example.com/custom.iso"
                        value={customIsoUrl}
                        onChange={(e) => setCustomIsoUrl(e.target.value)}
                        className="w-full px-3 py-2 bg-zinc-900 border border-zinc-800 rounded-md text-zinc-100 text-xs focus:border-indigo-500 focus:outline-none"
                      />
                      <button
                        onClick={handleCacheBaseIso}
                        disabled={isDownloadingBase || (status?.base_iso_progress !== undefined && status.base_iso_progress >= 0) || !customIsoUrl}
                        className="w-full py-2 text-xs font-bold bg-zinc-800 hover:bg-zinc-700 text-zinc-100 rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
                      >
                        {isDownloadingBase || (status?.base_iso_progress !== undefined && status.base_iso_progress >= 0) ? (t('downloadProgress') || 'Downloading...') : (t('downloadFromUrl') || 'DOWNLOAD FROM URL')}
                      </button>
                    </div>
                  )}

                  {isoSourceType === 'upload' && (
                    <div className="flex flex-col gap-2">
                      <input 
                        type="file" 
                        accept=".iso"
                        ref={fileInputRef}
                        className="w-full text-xs text-zinc-400 file:mr-3 file:py-1.5 file:px-3 file:rounded-md file:border-0 file:text-xs file:font-bold file:bg-zinc-800 file:text-zinc-100 hover:file:bg-zinc-700 transition-colors cursor-pointer"
                      />
                      <button
                        onClick={handleUpload}
                        disabled={isUploading}
                        className="w-full py-2 text-xs font-bold bg-zinc-800 hover:bg-zinc-700 text-zinc-100 rounded-md transition-colors disabled:opacity-50 cursor-pointer"
                      >
                        {isUploading ? `${t('uploadProgressText') || 'Uploading file...'} (${uploadProgress}%)` : (t('uploadIso') || 'UPLOAD ISO')}
                      </button>
                    </div>
                  )}

                  {/* Download Progress */}
                  {status?.base_iso_progress !== undefined && status.base_iso_progress >= 0 && (
                    <div className="mt-2 w-full">
                      <div className="flex justify-between items-center text-[10px] font-semibold mb-1">
                        <span className="text-zinc-400">
                          {status.base_iso_progress === 100
                            ? (t('validating') || 'Validating...')
                            : `${t('downloadProgress') || 'Downloading...'} ${status.base_iso_speed ? `(${status.base_iso_speed})` : ''}`
                          }
                        </span>
                        <span className="text-sky-400">{status.base_iso_progress}%</span>
                      </div>
                      <div className="w-full h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                        <div 
                          className="h-full bg-gradient-to-r from-sky-400 to-indigo-500 rounded-full transition-all duration-1000 ease-out"
                          style={{ width: `${status.base_iso_progress}%` }}
                        />
                      </div>
                    </div>
                  )}
                  
                  {/* Upload Progress */}
                  {isUploading && (
                    <div className="mt-2 w-full">
                      <div className="flex justify-between items-center text-[10px] font-semibold mb-1">
                        <span className="text-zinc-400">{t('uploadProgressText') || 'Uploading file...'}</span>
                        <span className="text-emerald-400">{uploadProgress}%</span>
                      </div>
                      <div className="w-full h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                        <div 
                          className="h-full bg-emerald-500 rounded-full transition-all duration-200 ease-out"
                          style={{ width: `${uploadProgress}%` }}
                        />
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Configuration & Compilation Panel */}
          <div className="p-6 bg-zinc-900 border border-zinc-800 rounded-2xl space-y-4 shadow-xl">
            <h3 className="text-sm font-bold text-zinc-50 flex items-center gap-2">
              {t('configPayloadTitle') || 'Configuration Payload'}
            </h3>
            <p className="text-xs text-zinc-400 mb-4 leading-relaxed">
              {t('configPayloadSub') || 'These settings will be injected into the Live-USB so the offline client can seamlessly sync.'}
            </p>

            <form onSubmit={handleGenerate} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('targetIpLabel') || 'Target Orchestrator IP / Domain'}</label>
                <DropdownTextInput
                  value={orchestratorIp}
                  onChange={setOrchestratorIp}
                  options={availableIps}
                  required
                />
              </div>

              {error && <div className="text-xs text-rose-400 bg-rose-500/10 border border-rose-500/20 p-3 rounded-lg">{error}</div>}
              {successMsg && <div className="text-xs text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 p-3 rounded-lg">{successMsg}</div>}

              <button
                type="submit"
                disabled={isGenerating || !status?.base_iso_cached}
                className="w-full flex items-center justify-center gap-2 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg font-bold text-sm tracking-wide shadow-lg disabled:opacity-50 transition-all cursor-pointer"
              >
                {isGenerating ? <RefreshCw className="animate-spin" size={18} /> : <Cpu size={18} />}
                {isGenerating ? (t('generatingUsb') || 'Generating...') : (t('generateUsbButton') || 'GENERATE LIVE-USB')}
              </button>
            </form>

            <div className="border-t border-zinc-800/80 pt-4 space-y-4">
              <div className="flex items-center justify-between p-3 bg-zinc-950 border border-zinc-800/80 rounded-xl">
                <div>
                  <div className="text-xs font-bold text-zinc-50">{t('compiledOfflineClient') || 'Compiled Offline Client'}</div>
                  <div className="text-[10px] text-zinc-505">technician_client_v1.iso</div>
                </div>
                {status?.client_iso_ready ? (
                  <span className="px-2 py-1 text-[10px] font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 rounded uppercase">{t('readyLabel') || 'Ready'}</span>
                ) : (
                  <span className="px-2 py-1 text-[10px] font-bold bg-zinc-800 text-zinc-400 border border-zinc-700 rounded uppercase">{t('notFoundLabel') || 'Not Found'}</span>
                )}
              </div>

              {status?.client_iso_ready && (
                <div className="space-y-2">
                  <button
                    type="button"
                    onClick={() => {
                      setError('');
                      setSuccessMsg('');
                      setShowIssueModal(true);
                    }}
                    className="w-full flex items-center justify-center gap-2 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg font-bold text-sm tracking-wide shadow-lg transition-all cursor-pointer hover:-translate-y-0.5 active:translate-y-0 active:scale-[0.98]"
                  >
                    <Cpu size={18} />
                    {t('issueKioskBtn') || 'Issue Kiosk'}
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setError('');
                      setSuccessMsg('');
                      fetchKiosks();
                      setShowHistoryModal(true);
                    }}
                    className="w-full flex items-center justify-center gap-2 py-2.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-100 border border-zinc-700 rounded-lg font-bold text-sm tracking-wide transition-all cursor-pointer hover:-translate-y-0.5 active:translate-y-0 active:scale-[0.98]"
                  >
                    <History size={18} />
                    {t('showIssuedIsosBtn') || 'Show Created ISOs'}
                  </button>

                  <p className="text-center text-[10px] text-zinc-550 mt-2 leading-relaxed">
                    {t('issueKioskDesc') || 'Creates a personalized kiosk with a unique dynamic pairing token.'}
                  </p>
                </div>
              )}
            </div>
          </div>

          {/* Offline Capabilities Card */}
          <div className="p-4 bg-indigo-50/50 dark:bg-indigo-500/10 border border-indigo-100 dark:border-indigo-500/20 rounded-xl flex items-start gap-3">
            <ShieldAlert className="text-indigo-600 dark:text-indigo-400 shrink-0 mt-0.5" size={18} />
            <div className="text-xs text-indigo-800 dark:text-indigo-200 leading-relaxed">
              <strong>{t('offlineCapabilities') || 'Offline Capabilities:'}</strong> {t('offlineCapabilitiesText') || 'The Live-USB bundles the identical Flasher module...'}
            </div>
          </div>
        </div>

        {/* Right Column (70% / 7 cols) */}
        <div className="lg:col-span-7">
          <KioskManagementSection onViewLogs={onViewLogs} />
        </div>
      </div>

      {/* Issue Kiosk Modal */}
      {showIssueModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fade-in">
          <div className="w-full max-w-md p-6 bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl space-y-4 animate-modal-in">
            <div>
              <h3 className="text-base font-bold text-zinc-50">{t('issueKioskModalTitle') || 'Issue New Kiosk'}</h3>
              <p className="text-[10px] text-zinc-400 font-semibold uppercase tracking-wider">
                {t('issueKioskModalSub') || 'Compile Live-CD ISO image for custom recipient'}
              </p>
            </div>
            
            <form onSubmit={handleIssueSubmit} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('issueKioskNameLabel') || 'Recipient Name'}</label>
                <input
                  type="text"
                  required
                  placeholder="e.g. John Doe"
                  value={issueName}
                  onChange={(e) => setIssueName(e.target.value)}
                  className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:border-indigo-500 focus:outline-none transition-colors"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('issueKioskContactLabel') || 'Contact (Phone / Telegram / Email)'}</label>
                <input
                  type="text"
                  required
                  placeholder="e.g. +1 555-0199 or @telegram"
                  value={issueContact}
                  onChange={(e) => setIssueContact(e.target.value)}
                  className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:border-indigo-500 focus:outline-none transition-colors"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('issueKioskCommentLabel') || 'Comment (Optional)'}</label>
                <textarea
                  rows={2}
                  placeholder="e.g. Technician kiosk for remote site recovery"
                  value={issueComment}
                  onChange={(e) => setIssueComment(e.target.value)}
                  className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:border-indigo-500 focus:outline-none transition-colors"
                />
              </div>

              {error && <div className="text-xs text-rose-400 bg-rose-500/10 border border-rose-500/20 p-3 rounded-lg">{error}</div>}

              <div className="flex justify-end gap-2 pt-2 border-t border-zinc-800">
                <button
                  type="button"
                  onClick={() => setShowIssueModal(false)}
                  className="px-4 py-2 text-xs font-semibold text-zinc-400 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors cursor-pointer"
                >
                  {t('cancel') || 'Cancel'}
                </button>
                <button
                  type="submit"
                  disabled={isIssuing}
                  className="px-4 py-2 text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg disabled:opacity-50 transition-colors cursor-pointer"
                >
                  {isIssuing ? t('saving') : (t('issueKioskSubmitBtn') || 'Issue & Generate ISO')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Kiosk ISO History / Created ISOs Modal */}
      {showHistoryModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fade-in">
          <div className="w-full max-w-2xl p-6 bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl space-y-4 animate-modal-in flex flex-col max-h-[85vh]">
            <div className="flex justify-between items-start pb-2 border-b border-zinc-800">
              <div>
                <h3 className="text-base font-bold text-zinc-50">{t('historyKiosksTitle') || 'Created Kiosk ISOs'}</h3>
                <p className="text-[10px] text-zinc-400 font-semibold uppercase tracking-wider">
                  {t('historyKiosksSub') || 'Compiled bootable client ISOs in repository cache'}
                </p>
              </div>
              <button 
                onClick={() => setShowHistoryModal(false)}
                className="text-zinc-500 hover:text-zinc-300 font-bold text-xs uppercase tracking-wide cursor-pointer"
              >
                {t('close') || 'Close'}
              </button>
            </div>

            <div className="flex-1 overflow-y-auto pr-1 space-y-3 min-h-[200px]">
              {kiosks.filter(k => k.iso_exists).length === 0 ? (
                <div className="py-12 text-center text-zinc-550">
                  <History className="mx-auto text-zinc-700 mb-3" size={32} />
                  <p className="text-xs font-semibold">{t('noKiosksIsosFound') || 'No compiled kiosk ISOs found'}</p>
                  <p className="text-[10px] text-zinc-500 mt-1">
                    {t('noKiosksIsosHint') || 'Use the "Issue Kiosk" button to generate a new customized kiosk.'}
                  </p>
                </div>
              ) : (
                kiosks.filter(k => k.iso_exists).map((kiosk) => (
                  <div key={kiosk.id} className="p-4 bg-zinc-950/40 border border-zinc-800/80 rounded-xl flex items-center justify-between gap-4 hover:border-zinc-800 transition-colors">
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-black text-zinc-205">{kiosk.name || 'Unnamed'}</span>
                        {kiosk.auth_token && (
                          <span className="px-1.5 py-0.5 text-[9px] font-black bg-indigo-500/10 text-indigo-400 border border-indigo-500/25 rounded uppercase">
                            {kiosk.auth_token}
                          </span>
                        )}
                      </div>
                      {kiosk.comment && (
                        <p className="text-[10px] text-zinc-400 leading-normal max-w-md">
                          {kiosk.comment}
                        </p>
                      )}
                      {kiosk.contact && (
                        <p className="text-[9px] text-zinc-500 font-mono">
                          {kiosk.contact}
                        </p>
                      )}
                      {kiosk.iso_path && (
                        <div className="text-[10px] text-zinc-400 space-y-1 font-sans mt-2 border-t border-zinc-800/50 pt-2">
                          <div className="flex items-start gap-1">
                            <span className="font-semibold text-zinc-500 shrink-0">File:</span>
                            <span className="font-mono text-zinc-205 font-black bg-zinc-900/40 px-1 py-0.5 rounded border border-zinc-800/20">
                              {kiosk.iso_path.split('/').pop()}
                            </span>
                          </div>
                          <div>
                            <span className="font-semibold text-zinc-500">{t('kioskCreatedAtLabel') || 'Creation Date'}:</span>{' '}
                            <span className="text-zinc-300 font-mono">
                              {kiosk.created_at ? new Date(kiosk.created_at).toLocaleString() : '—'}
                            </span>
                          </div>
                          <div>
                            <span className="font-semibold text-zinc-500">{t('isoSizeLabel') || 'Size'}:</span>{' '}
                            <span className="text-emerald-400 font-bold font-mono">
                              {formatBytes(kiosk.iso_size)}
                            </span>
                          </div>
                          <div className="flex items-start gap-1 pt-1">
                            <span className="font-semibold text-zinc-500 shrink-0">{t('isoLocationLabel') || 'Location'}:</span>
                            <code className="text-[9px] font-mono text-zinc-400 bg-zinc-900/60 px-1 py-0.5 rounded border border-zinc-800/30 break-all select-all">
                              {kiosk.iso_path}
                            </code>
                          </div>
                        </div>
                      )}
                    </div>

                    <div className="flex items-center gap-2 shrink-0">
                      <a
                        href={`/api/iso/kiosks/${kiosk.id}/download`}
                        className="p-2 bg-indigo-600/10 hover:bg-indigo-600/20 border border-indigo-500/20 text-indigo-400 hover:text-indigo-300 rounded-lg transition-colors cursor-pointer"
                        title={t('kioskActionDownload') || 'Download'}
                      >
                        <Download size={14} />
                      </a>
                      <button
                        onClick={() => handleDeleteKiosk(kiosk.id)}
                        className="p-2 bg-rose-500/10 hover:bg-rose-500/20 border border-rose-500/20 text-rose-400 hover:text-rose-350 rounded-lg transition-colors cursor-pointer"
                        title={t('deleteLabel') || 'Delete'}
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>

            <div className="flex justify-between items-center pt-3 border-t border-zinc-800">
              {status?.iso_cache_free_space !== undefined && status?.iso_cache_total_space !== undefined && (
                <div className="text-[10px] font-bold text-zinc-400 uppercase tracking-wide">
                  {t('isoFreeSpaceLabel') || 'Free space in repository:'}{' '}
                  <span className="text-indigo-400 font-mono">
                    {formatBytes(status.iso_cache_free_space)}
                  </span>{' '}
                  <span className="text-zinc-600 font-normal">/</span>{' '}
                  <span className="text-zinc-500 font-mono">
                    {formatBytes(status.iso_cache_total_space)}
                  </span>
                </div>
              )}
              <button
                type="button"
                onClick={() => setShowHistoryModal(false)}
                className="px-4 py-2 text-xs font-semibold text-zinc-400 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors cursor-pointer ml-auto"
              >
                {t('close') || 'Close'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
