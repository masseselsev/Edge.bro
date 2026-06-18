import React, { useState, useEffect } from 'react';
import { Download, Cpu, RefreshCw, CheckCircle, ShieldAlert } from 'lucide-react';
import { DropdownTextInput } from './SearchableSelect';
import { useTranslation } from '../context/TranslationContext';
import KioskManagementSection from './KioskManagementSection';

interface IsoStatus {
  base_iso_cached: boolean;
  client_iso_ready: boolean;
  base_iso_progress?: number;
  base_iso_speed?: string;
}

const generateRandomToken = (): string => {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  const block1 = Array.from({ length: 4 }, () => chars[Math.floor(Math.random() * chars.length)]).join('');
  const block2 = Array.from({ length: 4 }, () => chars[Math.floor(Math.random() * chars.length)]).join('');
  return `${block1}-${block2}`;
};

interface ClientIsoTabProps {
  onViewLogs: (taskId: string, title: string) => void;
}

export default function ClientIsoTab({ onViewLogs }: ClientIsoTabProps) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<IsoStatus | null>(null);
  const [orchestratorIp, setOrchestratorIp] = useState(window.location.hostname);
  const [authToken, setAuthToken] = useState(() => generateRandomToken());
  const [availableIps, setAvailableIps] = useState<string[]>([]);
  const [isGenerating, setIsGenerating] = useState(false);
  const [isDownloadingBase, setIsDownloadingBase] = useState(false);
  const [error, setError] = useState('');
  const [successMsg, setSuccessMsg] = useState('');
  const [subTab, setSubTab] = useState<'generator' | 'kiosks'>('generator');

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
          if (data.available_ips) {
            setAvailableIps(data.available_ips);
          }
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
          auth_token: authToken
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

      {/* Sub-tab Selection */}
      <div className="flex border-b border-zinc-800 pb-px mb-6 animate-fade-in">
        <button
          onClick={() => setSubTab('generator')}
          className={`px-4 py-2 text-xs font-bold border-b-2 transition-all cursor-pointer ${
            subTab === 'generator'
              ? 'border-indigo-500 text-indigo-400'
              : 'border-transparent text-zinc-400 hover:text-zinc-200'
          }`}
        >
          {t('isoGeneratorTab') || 'ISO Generator'}
        </button>
        <button
          onClick={() => setSubTab('kiosks')}
          className={`px-4 py-2 text-xs font-bold border-b-2 transition-all cursor-pointer ${
            subTab === 'kiosks'
              ? 'border-indigo-500 text-indigo-400'
              : 'border-transparent text-zinc-400 hover:text-zinc-200'
          }`}
        >
          {t('kioskManagementTab') || 'Kiosk Control'}
        </button>
      </div>

      {subTab === 'kiosks' ? (
        <div className="animate-fade-in">
          <KioskManagementSection />
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 animate-fade-in">
        {/* Configuration Panel */}
        <div className="p-6 bg-zinc-900 border border-zinc-800 rounded-2xl space-y-4 shadow-xl">
          <h3 className="text-sm font-bold text-zinc-50 flex items-center gap-2">
            {t('configPayloadTitle')}
          </h3>
          <p className="text-xs text-zinc-400 mb-4">
            {t('configPayloadSub')}
          </p>

          <form onSubmit={handleGenerate} className="space-y-4">
            <div>
              <label className="block text-xs font-semibold text-zinc-400 mb-1.5">{t('targetIpLabel')}</label>
              <DropdownTextInput
                value={orchestratorIp}
                onChange={setOrchestratorIp}
                options={availableIps}
                required
              />
            </div>

            <div>
              <div className="flex justify-between items-center mb-1.5">
                <label className="block text-xs font-semibold text-zinc-400">{t('apiTokenLabel')}</label>
              </div>
              <input
                type="text"
                required
                value={authToken}
                onChange={(e) => setAuthToken(e.target.value)}
                className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-sm focus:border-indigo-500 focus:outline-none transition-colors"
              />
            </div>

            {error && <div className="text-xs text-rose-400 bg-rose-500/10 border border-rose-500/20 p-3 rounded-lg">{error}</div>}
            {successMsg && <div className="text-xs text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 p-3 rounded-lg">{successMsg}</div>}

            <button
              type="submit"
              disabled={isGenerating || !status?.base_iso_cached}
              className="w-full flex items-center justify-center gap-2 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg font-bold text-sm tracking-wide shadow-lg disabled:opacity-50 transition-all"
            >
              {isGenerating ? <RefreshCw className="animate-spin" size={18} /> : <Cpu size={18} />}
              {isGenerating ? t('generatingUsb') : t('generateUsbButton')}
            </button>
          </form>
        </div>

        {/* Status & Download Panel */}
        <div className="space-y-6">
          <div className="p-6 bg-zinc-900 border border-zinc-800 rounded-2xl shadow-xl">
            <h3 className="text-sm font-bold text-zinc-50 mb-4">{t('pipelineStatus')}</h3>
            
            <div className="space-y-4">
              <div className="p-3 bg-zinc-950 border border-zinc-800/80 rounded-xl">
                <div className="flex items-center justify-between mb-3">
                  <div>
                    <div className="text-xs font-bold text-zinc-50">{t('baseIsoCache')}</div>
                    <div className="text-[10px] text-zinc-500">
                      {status?.base_iso_cached ? t('isoReady') : t('selectIsoSource')}
                    </div>
                  </div>
                  {status?.base_iso_cached && (
                    <div className="flex items-center gap-2">
                      <CheckCircle className="text-emerald-400" size={20} />
                      <button 
                        onClick={handleClearCache}
                        className="p-1 hover:bg-rose-500/20 text-rose-400 rounded transition-colors"
                        title={t('clearCachedIso')}
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
                        {t('officialTab')}
                      </button>
                      <button
                        type="button"
                        onClick={() => setIsoSourceType('url')}
                        className={`flex-1 py-1.5 text-[10px] font-bold rounded-md uppercase cursor-pointer transition-colors ${isoSourceType === 'url' ? 'bg-indigo-600 text-white' : 'text-zinc-400 hover:text-zinc-50 hover:bg-zinc-800'}`}
                      >
                        {t('customUrlTab')}
                      </button>
                      <button
                        type="button"
                        onClick={() => setIsoSourceType('upload')}
                        className={`flex-1 py-1.5 text-[10px] font-bold rounded-md uppercase cursor-pointer transition-colors ${isoSourceType === 'upload' ? 'bg-indigo-600 text-white' : 'text-zinc-400 hover:text-zinc-50 hover:bg-zinc-800'}`}
                      >
                        {t('uploadTab')}
                      </button>
                    </div>

                    {isoSourceType === 'official' && (
                      <div className="flex flex-col gap-2">
                        <div className="text-[10px] text-zinc-400">debian-live-testing-amd64-xfce.iso (4GB)</div>
                        <button
                          onClick={handleCacheBaseIso}
                          disabled={isDownloadingBase || (status?.base_iso_progress !== undefined && status.base_iso_progress >= 0)}
                          className="w-full py-2 text-xs font-bold bg-zinc-800 hover:bg-zinc-700 text-zinc-100 rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          {isDownloadingBase || (status?.base_iso_progress !== undefined && status.base_iso_progress >= 0) ? t('downloadProgress') : t('startDownload')}
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
                          className="w-full py-2 text-xs font-bold bg-zinc-800 hover:bg-zinc-700 text-zinc-100 rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          {isDownloadingBase || (status?.base_iso_progress !== undefined && status.base_iso_progress >= 0) ? t('downloadProgress') : t('downloadFromUrl')}
                        </button>
                      </div>
                    )}

                    {isoSourceType === 'upload' && (
                      <div className="flex flex-col gap-2">
                        <input 
                          type="file" 
                          accept=".iso"
                          ref={fileInputRef}
                          className="w-full text-xs text-zinc-400 file:mr-3 file:py-1.5 file:px-3 file:rounded-md file:border-0 file:text-xs file:font-bold file:bg-zinc-800 file:text-zinc-100 hover:file:bg-zinc-700 transition-colors"
                        />
                        <button
                          onClick={handleUpload}
                          disabled={isUploading}
                          className="w-full py-2 text-xs font-bold bg-zinc-800 hover:bg-zinc-700 text-zinc-100 rounded-md transition-colors disabled:opacity-50"
                        >
                          {isUploading ? `${t('uploadProgressText')} (${uploadProgress}%)` : t('uploadIso')}
                        </button>
                      </div>
                    )}

                    {/* Download Progress */}
                    {status?.base_iso_progress !== undefined && status.base_iso_progress >= 0 && (
                      <div className="mt-2 w-full">
                        <div className="flex justify-between items-center text-[10px] font-semibold mb-1">
                          <span className="text-zinc-400">
                            {status.base_iso_progress === 100
                              ? t('validating')
                              : `${t('downloadProgress')} ${status.base_iso_speed ? `(${status.base_iso_speed})` : ''}`
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
                          <span className="text-zinc-400">{t('uploadProgressText')}</span>
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

              <div className="flex items-center justify-between p-3 bg-zinc-950 border border-zinc-800/80 rounded-xl">
                <div>
                  <div className="text-xs font-bold text-zinc-50">{t('compiledOfflineClient')}</div>
                  <div className="text-[10px] text-zinc-500">technician_client_v1.iso</div>
                </div>
                {status?.client_iso_ready ? (
                  <span className="px-2 py-1 text-[10px] font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 rounded uppercase">{t('readyLabel')}</span>
                ) : (
                  <span className="px-2 py-1 text-[10px] font-bold bg-zinc-800 text-zinc-500 border border-zinc-700 rounded uppercase">{t('notFoundLabel')}</span>
                )}
              </div>
            </div>

            {status?.client_iso_ready && (
              <div className="mt-6">
                <a
                  href="/api/iso/download"
                  download
                  className="w-full flex items-center justify-center gap-2 py-3 bg-emerald-600 hover:bg-emerald-500 text-white rounded-xl font-bold text-sm tracking-wide shadow-lg transition-all"
                >
                  <Download size={18} />
                  {t('downloadIsoImage')}
                </a>
                <p className="text-center text-[10px] text-zinc-500 mt-2">
                  {t('flashInstructions').split(/(Rufus|balenaEtcher)/).map((part, index) => {
                    if (part === 'Rufus') {
                      return <a key={index} href="https://rufus.ie/en/" target="_blank" rel="noopener noreferrer" className="text-indigo-400 hover:text-indigo-300 transition-colors underline">Rufus</a>;
                    }
                    if (part === 'balenaEtcher') {
                      return <a key={index} href="https://etcher.balena.io/" target="_blank" rel="noopener noreferrer" className="text-indigo-400 hover:text-indigo-300 transition-colors underline">balenaEtcher</a>;
                    }
                    return part;
                  })}
                </p>
              </div>
            )}
          </div>
          
          <div className="p-4 bg-indigo-50/50 dark:bg-indigo-500/10 border border-indigo-100 dark:border-indigo-500/20 rounded-xl flex items-start gap-3">
            <ShieldAlert className="text-indigo-600 dark:text-indigo-400 shrink-0 mt-0.5" size={18} />
            <div className="text-xs text-indigo-800 dark:text-indigo-200 leading-relaxed">
              <strong>{t('offlineCapabilities')}</strong> {t('offlineCapabilitiesText')}
            </div>
          </div>
        </div>
      </div>
      )}

    </div>
  );
}
