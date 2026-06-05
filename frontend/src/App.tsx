import { useState, useEffect } from 'react';
import { Server, HardDrive, History, Settings as Gear, Terminal, Cpu } from 'lucide-react';
import FleetTab from './components/FleetTab';
import FlasherTab from './components/FlasherTab';
import HistoryTab from './components/HistoryTab';
import LogsTab from './components/LogsTab';
import SettingsTab from './components/SettingsTab';
import ClientIsoTab from './components/ClientIsoTab';
import TaskLogsModal from './components/TaskLogsModal';

type Tab = 'fleet' | 'flasher' | 'history' | 'logs' | 'settings' | 'clientiso';

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('fleet');
  const [logTaskId, setLogTaskId] = useState<string | null>(null);
  const [logTaskTitle, setLogTaskTitle] = useState<string>('');
  
  const [showIpPromptModal, setShowIpPromptModal] = useState(false);
  const [orchestratorIp, setOrchestratorIp] = useState('');
  const [settings, setSettings] = useState<any>(null);
  const [savingIp, setSavingIp] = useState(false);
  const [appVersion, setAppVersion] = useState('v0.3beta');

  useEffect(() => {
    // Fetch current app version from API
    fetch('/api/version')
      .then(res => res.json())
      .then(data => {
        if (data && data.version) {
          setAppVersion(data.version);
        }
      })
      .catch(err => console.error('Error fetching version:', err));

    // Fetch current settings on mount
    fetch('/api/settings')
      .then(res => res.json())
      .then(sett => {
        setSettings(sett);
        setOrchestratorIp(sett.orchestrator_ip || '');
        
        // Check if nodes list is empty on mount
        fetch('/api/nodes')
          .then(res => res.json())
          .then(nodes => {
            if (nodes.length === 0) {
              setShowIpPromptModal(true);
            }
          })
          .catch(err => console.error(err));
      })
      .catch(err => console.error(err));
  }, []);

  const handleSaveIp = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!settings) return;
    setSavingIp(true);
    try {
      const res = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...settings,
          orchestrator_ip: orchestratorIp
        })
      });
      if (res.ok) {
        setShowIpPromptModal(false);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setSavingIp(false);
    }
  };

  const handleViewLogs = (taskId: string, title: string) => {
    setLogTaskId(taskId);
    setLogTaskTitle(title);
  };

  const renderTabContent = () => {
    const tz = settings?.timezone || 'Browser Local';
    switch (activeTab) {
      case 'flasher':
        return <FlasherTab onViewLogs={handleViewLogs} timezone={tz} />;
      case 'clientiso':
        return <ClientIsoTab />;
      case 'history':
        return <HistoryTab onViewLogs={handleViewLogs} timezone={tz} />;
      case 'logs':
        return <LogsTab onViewLogs={handleViewLogs} timezone={tz} />;
      case 'settings':
        return <SettingsTab onSettingsUpdated={setSettings} />;
      case 'fleet':
      default:
        return <FleetTab onViewLogs={handleViewLogs} timezone={tz} />;
    }
  };

  return (
    <div className="min-h-screen bg-[#0b0f19] text-zinc-100 flex flex-col font-sans select-none">
      {/* Global Header */}
      <header className="bg-zinc-900/60 backdrop-blur-md border-b border-zinc-800/80 sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-indigo-600 rounded-lg shadow-indigo-600/20 shadow-md">
              <Server className="text-white" size={20} />
            </div>
            <div>
              <h1 className="text-base font-bold text-white tracking-tight leading-none flex items-center gap-2">
                Borg Restore Orchestrator
                <span className="text-[10px] bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 px-1.5 py-0.5 rounded font-mono font-bold">{appVersion}</span>
              </h1>
              <p className="text-[10px] text-zinc-500 font-semibold mt-0.5 uppercase tracking-wider">Fleet Edge Bare-Metal Flasher</p>
            </div>
          </div>

          {/* Tab Navigation */}
          <nav className="flex items-center gap-1 bg-zinc-950 p-1 rounded-xl border border-zinc-800/60">
            <button
              onClick={() => setActiveTab('fleet')}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all ${
                activeTab === 'fleet'
                  ? 'bg-zinc-900 text-white shadow-sm border border-zinc-800'
                  : 'text-zinc-400 hover:text-zinc-100'
              }`}
            >
              <Server size={14} /> Fleet
            </button>
            <button
              onClick={() => setActiveTab('flasher')}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all ${
                activeTab === 'flasher'
                  ? 'bg-zinc-900 text-white shadow-sm border border-zinc-800'
                  : 'text-zinc-400 hover:text-zinc-100'
              }`}
            >
              <HardDrive size={14} /> Flasher
            </button>
            <button
              onClick={() => setActiveTab('clientiso')}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all ${
                activeTab === 'clientiso'
                  ? 'bg-zinc-900 text-white shadow-sm border border-zinc-800'
                  : 'text-zinc-400 hover:text-zinc-100'
              }`}
            >
              <Cpu size={14} /> Technician Kiosk
            </button>
            <button
              onClick={() => setActiveTab('history')}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all ${
                activeTab === 'history'
                  ? 'bg-zinc-900 text-white shadow-sm border border-zinc-800'
                  : 'text-zinc-400 hover:text-zinc-100'
              }`}
            >
              <History size={14} /> History
            </button>
            <button
              onClick={() => setActiveTab('logs')}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all ${
                activeTab === 'logs'
                  ? 'bg-zinc-900 text-white shadow-sm border border-zinc-800'
                  : 'text-zinc-400 hover:text-zinc-100'
              }`}
            >
              <Terminal size={14} /> Logs
            </button>
            <button
              onClick={() => setActiveTab('settings')}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all ${
                activeTab === 'settings'
                  ? 'bg-zinc-900 text-white shadow-sm border border-zinc-800'
                  : 'text-zinc-400 hover:text-zinc-100'
              }`}
            >
              <Gear size={14} /> Settings
            </button>
          </nav>

          <div className="text-right">
            <span className="text-[10px] bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 px-2.5 py-1 rounded-full font-bold uppercase tracking-wider">
              System Online
            </span>
          </div>
        </div>
      </header>

      {/* Main Body */}
      <main className="flex-1 max-w-7xl w-full mx-auto px-6 py-8">
        {renderTabContent()}
      </main>

      {/* Active task console log stream overlay modal */}
      {logTaskId && (
        <TaskLogsModal
          taskId={logTaskId}
          title={logTaskTitle}
          onClose={() => setLogTaskId(null)}
        />
      )}

      {/* IP Prompt Modal when there are no nodes */}
      {showIpPromptModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fade-in">
          <div className="w-full max-w-md p-6 bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl space-y-4 animate-modal-in">
            <div className="flex items-center gap-3 border-b border-zinc-800 pb-3">
              <div className="p-2 bg-amber-500/10 text-amber-400 border border-amber-500/20 rounded-lg">
                <Gear size={20} />
              </div>
              <div>
                <h3 className="text-base font-bold text-white leading-tight">Welcome & Setup</h3>
                <p className="text-[10px] text-zinc-400 font-semibold uppercase tracking-wider">Configure Orchestrator IP</p>
              </div>
            </div>

            <p className="text-xs text-zinc-300 leading-relaxed font-medium">
              No edge nodes have been registered in the database yet. To ensure new nodes can communicate with this orchestrator and transfer backups successfully, please verify and set the **Orchestrator IP Address** below.
            </p>

            <form onSubmit={handleSaveIp} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-zinc-400 mb-1.5">Orchestrator IP Address</label>
                <input
                  type="text"
                  required
                  placeholder="e.g. 192.168.222.2 (IP accessible to edge nodes)"
                  value={orchestratorIp}
                  onChange={(e) => setOrchestratorIp(e.target.value)}
                  className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-white text-sm focus:border-indigo-500 focus:outline-none"
                />
                <p className="text-[10px] text-zinc-500 mt-1">
                  Ensure this is the IP address of this server that edge nodes can reach over the network.
                </p>
              </div>

              <div className="flex justify-end gap-2 pt-2 border-t border-zinc-800">
                <button
                  type="button"
                  onClick={() => setShowIpPromptModal(false)}
                  className="px-4 py-2 text-xs font-semibold text-zinc-400 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors"
                >
                  Skip
                </button>
                <button
                  type="submit"
                  disabled={savingIp}
                  className="px-4 py-2 text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg disabled:opacity-50 transition-colors"
                >
                  {savingIp ? 'Saving...' : 'Save & Continue'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
