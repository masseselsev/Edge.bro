import React, { useState } from 'react';

interface Node {
  id: number;
  hostname: string;
  ip_address: string;
  ssh_port: number;
  status: string;
}

interface AddNodeModalProps {
  onClose: () => void;
  onSubmit: (payload: any) => Promise<void>;
  submitting: boolean;
  error: string;
}

export function AddNodeModal({ onClose, onSubmit, submitting, error }: AddNodeModalProps) {
  const [hostname, setHostname] = useState('');
  const [ipAddress, setIpAddress] = useState('');
  const [sshPort, setSshPort] = useState(2222);
  const [username, setUsername] = useState('root');
  const [password, setPassword] = useState('admin');
  const [autoDetectHostname, setAutoDetectHostname] = useState(false);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit({
      hostname,
      ip_address: ipAddress,
      ssh_port: sshPort,
      bootstrap_user: username,
      bootstrap_password: password,
      auto_detect_hostname: autoDetectHostname
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-fade-in">
      <div className="w-full max-w-md p-6 bg-zinc-900 border border-zinc-800 rounded-2xl shadow-xl space-y-4 animate-modal-in">
        <h3 className="text-lg font-bold text-white">Add Node (Auto-Provision)</h3>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="flex items-center gap-2 mb-2 bg-zinc-950 p-2.5 rounded-lg border border-zinc-800">
            <input
              type="checkbox"
              id="autoDetectHostname"
              checked={autoDetectHostname}
              onChange={(e) => setAutoDetectHostname(e.target.checked)}
              className="rounded border-zinc-800 bg-zinc-950 text-indigo-600 focus:ring-indigo-500 h-4 w-4"
            />
            <label htmlFor="autoDetectHostname" className="text-xs font-semibold text-zinc-300 cursor-pointer select-none">
              Auto-detect hostname from device
            </label>
          </div>

          {!autoDetectHostname && (
            <div>
              <label className="block text-xs font-medium text-zinc-400 mb-1">Hostname</label>
              <input
                type="text"
                required
                placeholder="edge-node-01"
                value={hostname}
                onChange={(e) => setHostname(e.target.value)}
                className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-white text-sm focus:border-indigo-500 focus:outline-none"
              />
            </div>
          )}
          
          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1">IP Address / Ranges / CIDR</label>
            <input
              type="text"
              required
              placeholder="e.g. 192.168.1.10, 192.168.1.50-60, 192.168.2.0/24"
              value={ipAddress}
              onChange={(e) => setIpAddress(e.target.value)}
              className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-white text-sm focus:border-indigo-500 focus:outline-none"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-zinc-400 mb-1">SSH Port</label>
              <input
                type="number"
                value={sshPort}
                onChange={(e) => setSshPort(parseInt(e.target.value) || 22)}
                className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-white text-sm focus:border-indigo-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-zinc-400 mb-1">Bootstrap User</label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-white text-sm focus:border-indigo-500 focus:outline-none"
              />
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1">Temporary Password (escalation)</label>
            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-white text-sm focus:border-indigo-500 focus:outline-none"
            />
          </div>

          {error && <div className="text-xs text-rose-400 bg-rose-500/10 border border-rose-500/20 p-2.5 rounded-lg">{error}</div>}

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-sm font-semibold text-zinc-400 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="px-4 py-2 text-sm font-semibold text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg disabled:opacity-50 transition-colors"
            >
              {submitting ? 'Registering...' : 'Provision Now'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

interface ProvisionNodeModalProps {
  node: Node;
  onClose: () => void;
  onSubmit: (payload: any) => Promise<void>;
  submitting: boolean;
  error: string;
}

export function ProvisionNodeModal({ node, onClose, onSubmit, submitting, error }: ProvisionNodeModalProps) {
  const [username, setUsername] = useState('root');
  const [password, setPassword] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit({
      bootstrap_user: username,
      bootstrap_password: password
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-fade-in">
      <div className="w-full max-w-md p-6 bg-zinc-900 border border-zinc-800 rounded-2xl shadow-xl space-y-4 animate-modal-in">
        <div>
          <h3 className="text-lg font-bold text-white">Manual Provision Node</h3>
          <p className="text-xs text-zinc-400">Trigger bootstrap for node "{node.hostname}" ({node.ip_address})</p>
        </div>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1">Bootstrap User</label>
            <input
              type="text"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-white text-sm focus:border-indigo-500 focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1">Temporary Password (escalation)</label>
            <input
              type="password"
              required
              placeholder="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-white text-sm focus:border-indigo-500 focus:outline-none"
            />
          </div>

          {error && <div className="text-xs text-rose-400 bg-rose-500/10 border border-rose-500/20 p-2.5 rounded-lg">{error}</div>}

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-sm font-semibold text-zinc-400 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="px-4 py-2 text-sm font-semibold text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg disabled:opacity-50 transition-colors"
            >
              {submitting ? 'Starting...' : 'Bootstrap Node'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

interface BackupCommentModalProps {
  node: Node;
  onClose: () => void;
  onSubmit: (comment: string) => Promise<void>;
}

export function BackupCommentModal({ node, onClose, onSubmit }: BackupCommentModalProps) {
  const [comment, setComment] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    await onSubmit(comment);
    setSubmitting(false);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-fade-in">
      <div className="w-full max-w-md p-6 bg-zinc-900 border border-zinc-800 rounded-2xl shadow-xl space-y-4 animate-modal-in">
        <div>
          <h3 className="text-lg font-bold text-white">Trigger Backup</h3>
          <p className="text-xs text-zinc-400">Add an optional description/comment for this backup snapshot of "{node.hostname}".</p>
        </div>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1">Backup Comment / Tag</label>
            <input
              type="text"
              placeholder="e.g. Before kernel upgrade, Stable release v1.2"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-white text-sm focus:border-indigo-500 focus:outline-none"
              autoFocus
            />
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-sm font-semibold text-zinc-400 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="px-4 py-2 text-sm font-semibold text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg disabled:opacity-50 transition-colors"
            >
              {submitting ? 'Starting...' : 'Backup Now'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
