import { useState } from 'react';
import { Server, HardDrive, History, Settings as Gear, Terminal } from 'lucide-react';
import FleetTab from './components/FleetTab';
import FlasherTab from './components/FlasherTab';
import HistoryTab from './components/HistoryTab';
import SettingsTab from './components/SettingsTab';
import TaskLogsModal from './components/TaskLogsModal';

type Tab = 'fleet' | 'flasher' | 'history' | 'settings';

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('fleet');
  const [logTaskId, setLogTaskId] = useState<string | null>(null);
  const [logTaskTitle, setLogTaskTitle] = useState<string>('');

  const handleViewLogs = (taskId: string, title: string) => {
    setLogTaskId(taskId);
    setLogTaskTitle(title);
  };

  const renderTabContent = () => {
    switch (activeTab) {
      case 'flasher':
        return <FlasherTab onViewLogs={handleViewLogs} />;
      case 'history':
        return <HistoryTab onViewLogs={handleViewLogs} />;
      case 'settings':
        return <SettingsTab />;
      case 'fleet':
      default:
        return <FleetTab onViewLogs={handleViewLogs} />;
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
              <h1 className="text-base font-bold text-white tracking-tight leading-none">Borg Restore Orchestrator</h1>
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
    </div>
  );
}
