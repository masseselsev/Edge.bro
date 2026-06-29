import React, { useState, useEffect, useRef } from 'react';
import { ShieldAlert, RefreshCw, Wifi, Globe, Key, Settings, X, Globe2 } from 'lucide-react';
import { useTranslation } from '../context/TranslationContext';
import jsQR from 'jsqr';

interface NetworkSettingsModalProps {
  onClose: () => void;
  initialStatus?: NetworkStatus | null;
}

interface WiredStatus {
  device: string;
  connected: boolean;
  ip: string | null;
  netmask: string | null;
  gateway: string | null;
  dns_servers: string[];
  mode: 'auto' | 'manual';
  dns_mode: 'auto' | 'manual';
}

interface WifiStatus {
  device: string;
  connected: boolean;
  ssid: string | null;
  signal: number;
}

interface NetworkStatus {
  wired: WiredStatus;
  wifi: WifiStatus;
}

interface WifiNetwork {
  ssid: string;
  signal: number;
  security: string;
  active: boolean;
}

interface VpnStatus {
  connected: boolean;
  ip: string | null;
  endpoint: string | null;
  allowed_ips: string | null;
  received_bytes: number;
  sent_bytes: number;
  last_handshake: number;
}

export default function NetworkSettingsModal({ onClose, initialStatus = null }: NetworkSettingsModalProps) {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<'wired' | 'wifi' | 'vpn'>('wired');
  const [status, setStatus] = useState<NetworkStatus | null>(initialStatus);
  const [wifiNetworks, setWifiNetworks] = useState<WifiNetwork[]>([]);
  const [scanning, setScanning] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState<string | null>(null);
  const [hasInitialized, setHasInitialized] = useState(!!initialStatus);

  // VPN Form & Scanner States
  const [vpnStatus, setVpnStatus] = useState<VpnStatus | null>(null);
  const [isScanningQr, setIsScanningQr] = useState(false);
  const [showManualInput, setShowManualInput] = useState(false);
  const [manualConfigText, setManualConfigText] = useState('');
  const [vpnConnecting, setVpnConnecting] = useState(false);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const animationFrameRef = useRef<number | null>(null);
  
  // Wired Form States
  const [wiredMode, setWiredMode] = useState<'auto' | 'manual'>(initialStatus?.wired?.mode || 'auto');
  const [ipAddress, setIpAddress] = useState(initialStatus?.wired?.ip || '');
  const [netmask, setNetmask] = useState(initialStatus?.wired?.netmask || '255.255.255.0');
  const [gateway, setGateway] = useState(initialStatus?.wired?.gateway || '');
  const [dnsMode, setDnsMode] = useState<'auto' | 'manual'>(initialStatus?.wired?.dns_mode || 'auto');
  const [dns1, setDns1] = useState(initialStatus?.wired?.dns_servers?.[0] || '');
  const [dns2, setDns2] = useState(initialStatus?.wired?.dns_servers?.[1] || '');

  // Wi-Fi States
  const [selectedSsid, setSelectedSsid] = useState<string | null>(null);
  const [wifiPassword, setWifiPassword] = useState('');
  const [showHiddenForm, setShowHiddenForm] = useState(false);
  const [hiddenSsid, setHiddenSsid] = useState('');
  const [hiddenSecurity, setHiddenSecurity] = useState('WPA2');

  useEffect(() => {
    fetchStatus();
    scanWifi();
    fetchVpnStatus();
    const interval = setInterval(() => {
      fetchStatus();
      fetchVpnStatus();
    }, 5000);
    return () => {
      clearInterval(interval);
      if (streamRef.current) {
        streamRef.current.getTracks().forEach(track => track.stop());
      }
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
      }
    };
  }, []);

  const fetchStatus = async () => {
    try {
      const res = await fetch('/api/network/status');
      if (!res.ok) return;
      const data: NetworkStatus = await res.json();
      setStatus(data);
      if (data.wired && !hasInitialized) {
        setWiredMode(data.wired.mode || 'auto');
        setIpAddress(data.wired.ip || '');
        setNetmask(data.wired.netmask || '255.255.255.0');
        setGateway(data.wired.gateway || '');
        setDnsMode(data.wired.dns_mode || 'auto');
        if (data.wired.dns_servers) {
          setDns1(data.wired.dns_servers[0] || '');
          setDns2(data.wired.dns_servers[1] || '');
        }
        setHasInitialized(true);
      }
    } catch (err) {
      console.error('Failed to fetch network status:', err);
    }
  };

  const scanWifi = async () => {
    setScanning(true);
    try {
      const res = await fetch('/api/network/wifi/scan');
      if (!res.ok) return;
      const data: WifiNetwork[] = await res.json();
      setWifiNetworks(data);
    } catch (err) {
      console.error('Failed to scan Wi-Fi:', err);
    } finally {
      setScanning(false);
    }
  };

  const fetchVpnStatus = async () => {
    try {
      const res = await fetch('/api/network/vpn/status');
      if (res.ok) {
        const data = await res.json();
        setVpnStatus(data);
      }
    } catch (err) {
      console.error('Failed to fetch VPN status:', err);
    }
  };

  const startScanner = async () => {
    setConnectError(null);
    setIsScanningQr(true);
    setShowManualInput(false);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        videoRef.current.setAttribute("playsinline", "true");
        videoRef.current.play();
        animationFrameRef.current = requestAnimationFrame(scanTick);
      }
    } catch (err: any) {
      setConnectError("Failed to access camera: " + err.message);
      setIsScanningQr(false);
    }
  };

  const stopScanner = () => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(track => track.stop());
      streamRef.current = null;
    }
    if (animationFrameRef.current) {
      cancelAnimationFrame(animationFrameRef.current);
      animationFrameRef.current = null;
    }
    setIsScanningQr(false);
  };

  const scanTick = () => {
    if (!videoRef.current || !canvasRef.current) return;
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (video.readyState === video.HAVE_ENOUGH_DATA) {
      const ctx = canvas.getContext('2d');
      if (ctx) {
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
        const code = jsQR(imageData.data, imageData.width, imageData.height, {
          inversionAttempts: "dontInvert",
        });
        if (code) {
          stopScanner();
          handleSaveVpnConfig(code.data);
          return;
        }
      }
    }
    animationFrameRef.current = requestAnimationFrame(scanTick);
  };

  const handleSaveVpnConfig = async (configText: string) => {
    setConnecting(true);
    setConnectError(null);
    try {
      const res = await fetch('/api/network/vpn', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config_text: configText })
      });
      const data = await res.json();
      if (res.ok && data.status === 'SUCCESS') {
        await fetchVpnStatus();
      } else {
        setConnectError(data.detail || data.error || 'Failed to save VPN configuration');
      }
    } catch (err: any) {
      setConnectError(err.message || 'Network error occurred');
    } finally {
      setConnecting(false);
    }
  };

  const handleToggleVpn = async (connect: boolean) => {
    setVpnConnecting(true);
    setConnectError(null);
    try {
      const endpoint = connect ? '/api/network/vpn/connect' : '/api/network/vpn/disconnect';
      const res = await fetch(endpoint, { method: 'POST' });
      const data = await res.json();
      if (res.ok && data.status === 'SUCCESS') {
        await fetchVpnStatus();
      } else {
        setConnectError(data.detail || data.error || 'Failed to toggle VPN connection');
      }
    } catch (err: any) {
      setConnectError(err.message || 'Network error occurred');
    } finally {
      setVpnConnecting(false);
    }
  };

  const handleDeleteVpn = async () => {
    if (!window.confirm("Are you sure you want to delete this VPN configuration profile?")) return;
    setConnecting(true);
    setConnectError(null);
    try {
      const res = await fetch('/api/network/vpn', { method: 'DELETE' });
      const data = await res.json();
      if (res.ok && data.status === 'SUCCESS') {
        setVpnStatus(null);
        await fetchVpnStatus();
      } else {
        setConnectError(data.detail || data.error || 'Failed to delete VPN configuration');
      }
    } catch (err: any) {
      setConnectError(err.message || 'Network error occurred');
    } finally {
      setConnecting(false);
    }
  };

  const handleApplyWired = async (e: React.FormEvent) => {
    e.preventDefault();
    setConnecting(true);
    setConnectError(null);
    try {
      const res = await fetch('/api/network/wired/configure', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mode: wiredMode,
          ip_address: wiredMode === 'manual' ? ipAddress : null,
          netmask: wiredMode === 'manual' ? netmask : null,
          gateway: wiredMode === 'manual' ? gateway : null,
          dns_mode: dnsMode,
          dns_servers: dnsMode === 'manual' ? [dns1, dns2].filter(Boolean) : null
        })
      });
      const data = await res.json();
      if (data.status === 'SUCCESS') {
        await fetchStatus();
        onClose();
      } else {
        setConnectError(data.error || 'Failed to configure wired network');
      }
    } catch (err: any) {
      setConnectError(err.message || 'Network error occurred');
    } finally {
      setConnecting(false);
    }
  };

  const handleConnectWifi = async (e: React.FormEvent) => {
    e.preventDefault();
    const ssid = showHiddenForm ? hiddenSsid : selectedSsid;
    if (!ssid) return;

    setConnecting(true);
    setConnectError(null);
    try {
      const res = await fetch('/api/network/wifi/connect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ssid,
          password: wifiPassword || null,
          hidden: showHiddenForm
        })
      });
      const data = await res.json();
      if (data.status === 'SUCCESS') {
        await fetchStatus();
        onClose();
      } else {
        setConnectError(data.error || 'Failed to connect to Wi-Fi');
      }
    } catch (err: any) {
      setConnectError(err.message || 'Network error occurred');
    } finally {
      setConnecting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fade-in">
      <div className="w-full max-w-lg bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl overflow-hidden animate-modal-in flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="p-4 border-b border-zinc-800 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 rounded-lg">
              <Settings size={18} />
            </div>
            <h3 className="text-sm font-bold text-zinc-50 uppercase tracking-wider">{t('networkSettingsTitle')}</h3>
          </div>
          <button onClick={onClose} className="text-zinc-400 hover:text-white transition-colors">
            <X size={18} />
          </button>
        </div>

        {/* Navigation Tabs */}
        <div className="flex bg-zinc-950/80 p-1 border-b border-zinc-800">
          <button
            onClick={() => { setActiveTab('wired'); stopScanner(); }}
            className={`flex-1 flex items-center justify-center gap-2 py-2 rounded-lg text-xs font-bold transition-all ${
              activeTab === 'wired'
                ? 'bg-zinc-900 text-white shadow-sm border border-zinc-800/80'
                : 'text-zinc-400 hover:text-zinc-100'
            }`}
          >
            <Globe size={14} /> {t('wiredEthernet')}
          </button>
          <button
            onClick={() => { setActiveTab('wifi'); scanWifi(); stopScanner(); }}
            className={`flex-1 flex items-center justify-center gap-2 py-2 rounded-lg text-xs font-bold transition-all ${
              activeTab === 'wifi'
                ? 'bg-zinc-900 text-white shadow-sm border border-zinc-800/80'
                : 'text-zinc-400 hover:text-zinc-100'
            }`}
          >
            <Wifi size={14} /> {t('wifiConnections')}
          </button>
          <button
            onClick={() => { setActiveTab('vpn'); stopScanner(); }}
            className={`flex-1 flex items-center justify-center gap-2 py-2 rounded-lg text-xs font-bold transition-all ${
              activeTab === 'vpn'
                ? 'bg-zinc-900 text-white shadow-sm border border-zinc-800/80'
                : 'text-zinc-400 hover:text-zinc-100'
            }`}
          >
            <Key size={14} /> {t('vpnWireguard')}
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5">
          {connectError && (
            <div className="mb-4 p-3 bg-rose-500/10 border border-rose-500/20 text-rose-400 text-xs rounded-lg flex items-center gap-2">
              <ShieldAlert size={14} />
              <span>{connectError}</span>
            </div>
          )}

          {activeTab === 'wired' ? (
            <form onSubmit={handleApplyWired} className="space-y-4">
              <div className="bg-zinc-950/50 border border-zinc-800/50 p-3.5 rounded-xl flex items-center justify-between">
                <div>
                  <span className="text-xs font-bold text-zinc-50 block">{t('wiredLinkState')}</span>
                  <span className="text-[10px] text-zinc-400 mt-1 block">
                    {status?.wired?.connected 
                      ? t('connectedWithParam').replace('{device}', status.wired.device).replace('{ip}', status.wired.ip || 'No IP') 
                      : t('disconnectedLabel')}
                  </span>
                </div>
                <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full uppercase ${
                  status?.wired?.connected ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-zinc-800 text-zinc-500'
                }`}>
                  {status?.wired?.connected ? t('connectedWithParam').split(' ')[0] : t('noLinkLabel')}
                </span>
              </div>

              <div className="space-y-3">
                <label className="text-[10px] text-zinc-400 font-bold uppercase tracking-wider block">{t('ipAssignmentMode')}</label>
                <div className="flex bg-zinc-950 p-1 border border-zinc-800/80 rounded-xl">
                  <button
                    type="button"
                    onClick={() => setWiredMode('auto')}
                    className={`flex-1 py-1.5 rounded-lg text-xs font-bold transition-all ${
                      wiredMode === 'auto' ? 'bg-indigo-600 text-white shadow-md' : 'text-zinc-400 hover:text-white'
                    }`}
                  >
                    {t('dynamicDhcp')}
                  </button>
                  <button
                    type="button"
                    onClick={() => setWiredMode('manual')}
                    className={`flex-1 py-1.5 rounded-lg text-xs font-bold transition-all ${
                      wiredMode === 'manual' ? 'bg-indigo-600 text-white shadow-md' : 'text-zinc-400 hover:text-white'
                    }`}
                  >
                    {t('staticIp')}
                  </button>
                </div>
              </div>

              {wiredMode === 'manual' && (
                <div className="grid grid-cols-2 gap-3 transition-all duration-300">
                  <div className="col-span-1">
                    <label className="text-[10px] text-zinc-400 font-bold mb-1.5 block">{t('ipAddress')}</label>
                    <input
                      type="text"
                      required
                      value={ipAddress}
                      onChange={e => setIpAddress(e.target.value)}
                      placeholder="e.g. 192.168.1.50"
                      className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-xs focus:border-indigo-500 focus:outline-none"
                    />
                  </div>
                  <div className="col-span-1">
                    <label className="text-[10px] text-zinc-400 font-bold mb-1.5 block">{t('subnetMask')}</label>
                    <input
                      type="text"
                      required
                      value={netmask}
                      onChange={e => setNetmask(e.target.value)}
                      placeholder="e.g. 255.255.255.0"
                      className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-xs focus:border-indigo-500 focus:outline-none"
                    />
                  </div>
                  <div className="col-span-2">
                    <label className="text-[10px] text-zinc-400 font-bold mb-1.5 block">{t('gateway')}</label>
                    <input
                      type="text"
                      value={gateway}
                      onChange={e => setGateway(e.target.value)}
                      placeholder={t('gatewayPlaceholder')}
                      className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-xs focus:border-indigo-500 focus:outline-none"
                    />
                  </div>
                </div>
              )}

              <div className="border-t border-zinc-800/80 pt-4 space-y-3">
                <div className="flex justify-between items-center">
                  <label className="text-[10px] text-zinc-400 font-bold uppercase tracking-wider block">{t('dnsMode')}</label>
                  <div className="flex gap-4">
                    <label className="text-xs text-zinc-300 flex items-center gap-1.5 cursor-pointer">
                      <input type="radio" name="dnsMode" checked={dnsMode === 'auto'} onChange={() => setDnsMode('auto')} className="accent-indigo-600" /> {t('automatic')}
                    </label>
                    <label className="text-xs text-zinc-300 flex items-center gap-1.5 cursor-pointer">
                      <input type="radio" name="dnsMode" checked={dnsMode === 'manual'} onChange={() => setDnsMode('manual')} className="accent-indigo-600" /> {t('manual')}
                    </label>
                  </div>
                </div>

                {dnsMode === 'manual' && (
                  <div className="grid grid-cols-2 gap-3 transition-all duration-300">
                    <input
                      type="text"
                      required
                      value={dns1}
                      onChange={e => setDns1(e.target.value)}
                      placeholder={t('primaryDns')}
                      className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-xs focus:border-indigo-500 focus:outline-none"
                    />
                    <input
                      type="text"
                      value={dns2}
                      onChange={e => setDns2(e.target.value)}
                      placeholder={t('secondaryDns')}
                      className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-xs focus:border-indigo-500 focus:outline-none"
                    />
                  </div>
                )}
              </div>

              <div className="flex justify-end gap-2 pt-3 border-t border-zinc-800">
                <button type="button" onClick={onClose} className="px-4 py-2 text-xs font-bold text-zinc-400 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors">{t('cancel')}</button>
                <button type="submit" className="px-4 py-2 text-xs font-bold text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors">{t('applyWiredConfig')}</button>
              </div>
            </form>
          ) : (
            <form onSubmit={handleConnectWifi} className="space-y-4">
              {!showHiddenForm ? (
                <>
                  <div className="flex justify-between items-center mb-1">
                    <span className="text-[10px] text-zinc-400 font-bold uppercase tracking-wider">{t('wirelessNetworks')}</span>
                    <button type="button" onClick={scanWifi} disabled={scanning} className="text-indigo-400 hover:text-indigo-300 text-xs font-bold flex items-center gap-1.5 transition-colors disabled:opacity-50">
                      <RefreshCw size={12} className={scanning ? 'animate-spin' : ''} /> {t('scan')}
                    </button>
                  </div>

                  <div className="space-y-2 max-h-[200px] overflow-y-auto pr-1">
                    {wifiNetworks.map((net, idx) => (
                      <div
                        key={idx}
                        onClick={() => { setSelectedSsid(net.ssid); setShowHiddenForm(false); }}
                        className={`p-3 rounded-xl border flex items-center justify-between cursor-pointer transition-all duration-200 ${
                          selectedSsid === net.ssid ? 'bg-indigo-600/10 border-indigo-500 text-zinc-100' : 'bg-zinc-950 border-zinc-800 text-zinc-300 hover:border-zinc-700'
                        }`}
                      >
                        <div className="flex items-center gap-2">
                          <Wifi size={14} className={net.active ? 'text-emerald-400' : 'text-zinc-500'} />
                          <span className="text-xs font-bold">{net.ssid}</span>
                        </div>
                        <div className="flex items-center gap-2">
                          {net.security !== 'Open' && <Key size={12} className="text-zinc-500" />}
                          <span className="text-[10px] font-bold text-zinc-400">{net.signal}%</span>
                        </div>
                      </div>
                    ))}

                    {wifiNetworks.length === 0 && !scanning && (
                      <p className="text-zinc-500 text-xs text-center py-4 font-semibold">{t('noWifiNetworksFound')}</p>
                    )}
                  </div>

                  <button
                    type="button"
                    onClick={() => { setShowHiddenForm(true); setSelectedSsid(null); }}
                    className="w-full p-2.5 bg-zinc-950 border border-zinc-800 border-dashed rounded-xl text-indigo-400 text-xs font-bold hover:border-zinc-700 transition-colors flex items-center justify-center gap-2"
                  >
                    {t('connectHiddenNetwork')}
                  </button>
                </>
              ) : (
                <div className="space-y-3">
                  <div className="flex justify-between items-center mb-1">
                    <span className="text-[10px] text-zinc-400 font-bold uppercase tracking-wider">{t('hiddenWirelessConfig')}</span>
                    <button type="button" onClick={() => setShowHiddenForm(false)} className="text-indigo-400 hover:text-indigo-300 text-xs font-bold transition-colors">
                      {t('backToList')}
                    </button>
                  </div>

                  <div className="space-y-3">
                    <div>
                      <label className="text-[10px] text-zinc-400 font-bold mb-1 block">{t('networkSsidName')}</label>
                      <input
                        type="text"
                        required
                        value={hiddenSsid}
                        onChange={e => setHiddenSsid(e.target.value)}
                        placeholder="e.g. MyHiddenNetwork"
                        className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-xs focus:border-indigo-500 focus:outline-none"
                      />
                    </div>
                    <div>
                      <label className="text-[10px] text-zinc-400 font-bold mb-1 block">{t('securityType')}</label>
                      <select
                        value={hiddenSecurity}
                        onChange={e => setHiddenSecurity(e.target.value)}
                        className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-xs focus:border-indigo-500 focus:outline-none"
                      >
                        <option value="WPA2">{t('wpaPersonalSecured')}</option>
                        <option value="Open">{t('openUnsecured')}</option>
                      </select>
                    </div>
                  </div>
                </div>
              )}

              {((selectedSsid && wifiNetworks.find(n => n.ssid === selectedSsid)?.security !== 'Open') || (showHiddenForm && hiddenSecurity !== 'Open')) && (
                <div className="space-y-1">
                  <label className="text-[10px] text-zinc-400 font-bold mb-1 block">{t('passwordLabel')}</label>
                  <input
                    type="password"
                    required
                    value={wifiPassword}
                    onChange={e => setWifiPassword(e.target.value)}
                    placeholder={t('wifiPasswordPlaceholder')}
                    className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-xs focus:border-indigo-500 focus:outline-none"
                  />
                </div>
              )}

              <div className="flex justify-end gap-2 pt-3 border-t border-zinc-800">
                <button type="button" onClick={onClose} className="px-4 py-2 text-xs font-bold text-zinc-400 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors">{t('cancel')}</button>
                <button
                  type="submit"
                  disabled={!selectedSsid && !showHiddenForm}
                  className="px-4 py-2 text-xs font-bold text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg disabled:opacity-50 transition-colors"
                >
                  {t('connectToWifi')}
                </button>
              </div>
            </form>
          )}

          {activeTab === 'vpn' && (
            <div className="space-y-4">
              {vpnStatus ? (
                /* Active VPN status view */
                <div className="space-y-4">
                  <div className="bg-zinc-950/50 border border-zinc-800/50 p-3.5 rounded-xl flex items-center justify-between animate-fade-in">
                    <div>
                      <span className="text-xs font-bold text-zinc-50 block">wg0.conf (WireGuard)</span>
                      <span className="text-[10px] text-zinc-400 mt-1 block">
                        {vpnStatus.connected ? `Connected • IP: ${vpnStatus.ip || 'No IP'}` : 'Disconnected'}
                      </span>
                    </div>
                    <button
                      type="button"
                      disabled={vpnConnecting}
                      onClick={() => handleToggleVpn(!vpnStatus.connected)}
                      className={`text-[10px] font-bold px-3 py-1 rounded-lg uppercase transition-colors cursor-pointer ${
                        vpnStatus.connected 
                          ? 'bg-rose-500/10 text-rose-400 border border-rose-500/20 hover:bg-rose-500/20' 
                          : 'bg-indigo-600 text-white hover:bg-indigo-500 shadow-md'
                      }`}
                    >
                      {vpnConnecting ? '...' : (vpnStatus.connected ? 'Disconnect' : 'Connect')}
                    </button>
                  </div>

                  <div className="border border-zinc-800/80 rounded-xl p-4 bg-zinc-950/20 space-y-3 font-mono text-[10px] text-zinc-400 animate-fade-in">
                    <div className="grid grid-cols-2 gap-2">
                      <div>Peer Endpoint:</div>
                      <div className="text-zinc-200 text-right overflow-hidden text-ellipsis whitespace-nowrap">{vpnStatus.endpoint || 'N/A'}</div>
                      <div>Allowed IPs:</div>
                      <div className="text-zinc-200 text-right overflow-hidden text-ellipsis whitespace-nowrap">{vpnStatus.allowed_ips || 'N/A'}</div>
                      <div>Bytes Received:</div>
                      <div className="text-emerald-400 text-right font-bold">{(vpnStatus.received_bytes / (1024 * 1024)).toFixed(2)} MB</div>
                      <div>Bytes Sent:</div>
                      <div className="text-emerald-400 text-right font-bold">{(vpnStatus.sent_bytes / (1024 * 1024)).toFixed(2)} MB</div>
                      <div>Last Handshake:</div>
                      <div className="text-zinc-200 text-right">
                        {vpnStatus.last_handshake > 0 ? `${vpnStatus.last_handshake} seconds ago` : 'Never'}
                      </div>
                    </div>
                  </div>

                  <div className="flex justify-end gap-2 pt-3 border-t border-zinc-800">
                    <button
                      type="button"
                      onClick={handleDeleteVpn}
                      className="px-3 py-1.5 text-xs font-bold text-rose-400 bg-rose-500/10 hover:bg-rose-500/20 border border-rose-500/20 rounded-lg transition-colors cursor-pointer"
                    >
                      Delete Profile
                    </button>
                    <button
                      type="button"
                      onClick={onClose}
                      className="px-4 py-1.5 text-xs font-bold text-zinc-400 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors"
                    >
                      Close
                    </button>
                  </div>
                </div>
              ) : (
                /* Scanner / Config input selector */
                <div className="space-y-4">
                  {isScanningQr ? (
                    /* Video scanner feed */
                    <div className="space-y-3 text-center">
                      <div className="relative aspect-video w-full bg-black rounded-xl overflow-hidden border border-zinc-800">
                        <video ref={videoRef} className="w-full h-full object-cover" />
                        <canvas ref={canvasRef} className="hidden" />
                        <div className="absolute inset-0 border-2 border-dashed border-indigo-500/50 pointer-events-none rounded-xl m-8"></div>
                      </div>
                      <div className="flex justify-center gap-2">
                        <button
                          type="button"
                          onClick={stopScanner}
                          className="px-4 py-1.5 text-xs font-bold text-zinc-400 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors cursor-pointer"
                        >
                          Cancel Scan
                        </button>
                      </div>
                    </div>
                  ) : showManualInput ? (
                    /* Textarea manually paste config */
                    <div className="space-y-3">
                      <div>
                        <label className="text-[10px] text-zinc-400 font-bold mb-1.5 block">Paste wg0.conf Contents</label>
                        <textarea
                          rows={10}
                          value={manualConfigText}
                          onChange={e => setManualConfigText(e.target.value)}
                          placeholder="[Interface]&#10;PrivateKey = ...&#10;Address = ...&#10;&#10;[Peer]&#10;PublicKey = ...&#10;Endpoint = ..."
                          className="w-full p-2.5 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-xs font-mono focus:border-indigo-500 focus:outline-none"
                        />
                      </div>
                      <div className="flex justify-end gap-2 pt-3 border-t border-zinc-800">
                        <button
                          type="button"
                          onClick={() => setShowManualInput(false)}
                          className="px-3 py-1.5 text-xs font-bold text-zinc-400 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors"
                        >
                          Back
                        </button>
                        <button
                          type="button"
                          disabled={!manualConfigText.trim()}
                          onClick={() => {
                            setShowManualInput(false);
                            handleSaveVpnConfig(manualConfigText);
                          }}
                          className="px-4 py-1.5 text-xs font-bold text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg disabled:opacity-50 transition-colors cursor-pointer"
                        >
                          Save Connection
                        </button>
                      </div>
                    </div>
                  ) : (
                    /* Initial missing profile chooser buttons */
                    <div className="border border-zinc-800/80 border-dashed rounded-xl p-8 text-center space-y-4 bg-zinc-950/20">
                      <div>
                        <span className="text-xs font-bold text-zinc-100 block">No VPN Profile Configured</span>
                        <span className="text-[10px] text-zinc-400 mt-1 block max-w-[280px] mx-auto leading-relaxed">
                          Scan a WireGuard QR code or paste standard configuration file text manually.
                        </span>
                      </div>
                      <div className="flex justify-center gap-2">
                        <button
                          type="button"
                          onClick={startScanner}
                          className="px-4 py-2 text-xs font-bold text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors shadow-md cursor-pointer"
                        >
                          📷 Scan QR Code
                        </button>
                        <button
                          type="button"
                          onClick={() => setShowManualInput(true)}
                          className="px-4 py-2 text-xs font-bold text-zinc-300 bg-zinc-800 hover:bg-zinc-700 rounded-lg border border-zinc-700 transition-colors cursor-pointer"
                        >
                          📝 Paste Config
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Loading / Connecting Overlay */}
      {connecting && (
        <div className="absolute inset-0 bg-black/80 z-50 flex flex-col items-center justify-center space-y-4 animate-fade-in">
          <RefreshCw className="animate-spin text-indigo-400" size={36} />
          <div className="text-center">
            <p className="text-sm font-bold text-white">{t('applyingConfig')}</p>
            <p className="text-[10px] text-zinc-400 mt-1 uppercase tracking-wider font-semibold">{t('configuringNetworkManager')}</p>
          </div>
        </div>
      )}
    </div>
  );
}
