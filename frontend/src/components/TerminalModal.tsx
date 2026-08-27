import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import { useTranslation } from '../context/TranslationContext';
import type { Node } from '../types';

interface TerminalModalProps {
  node: Node;
  onClose: () => void;
}

// Keys match the close_reason strings routers/terminal.py sends as the WS
// close frame's reason — anything else (or none) falls back to the generic
// terminalClosed message.
const CLOSE_REASON_KEYS: Record<string, string> = {
  idle: 'terminalCloseIdle',
  'remote closed': 'terminalCloseRemoteClosed',
  'client closed': 'terminalCloseClientClosed',
};

export default function TerminalModal({ node, onClose }: TerminalModalProps) {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [status, setStatus] = useState<'connecting' | 'open' | 'closed' | 'failed'>('connecting');
  const [closeReason, setCloseReason] = useState<string | null>(null);

  useEffect(() => {
    if (!containerRef.current) return undefined;

    const term = new Terminal({ cursorBlink: true, fontSize: 13 });
    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    term.open(containerRef.current);
    fitAddon.fit();
    term.focus();

    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(`${proto}//${window.location.host}/api/nodes/${node.id}/terminal`);
    socket.binaryType = 'arraybuffer';

    const sendResize = () => {
      fitAddon.fit();
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }));
      }
    };

    socket.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        term.write(new Uint8Array(event.data));
      }
    };
    // A socket that closes without ever opening never reached the backend at
    // all — a rejected handshake, or a proxy in front that did not forward the
    // upgrade. Reported separately from a session that ran and ended, since
    // "closed" for both is what makes a failure look like a normal exit.
    let everOpened = false;

    socket.onopen = () => {
      everOpened = true;
      setStatus('open');
      sendResize();
      term.focus();
    };
    socket.onclose = (event) => {
      setStatus(everOpened ? 'closed' : 'failed');
      setCloseReason(event.reason || null);
    };
    socket.onerror = () => {
      setStatus((prev) => (prev === 'open' ? 'closed' : 'failed'));
    };

    const dataDisposable = term.onData((data) => {
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(new TextEncoder().encode(data));
      }
    });

    // Refits on any real size change of the terminal's own box -- window
    // resizes, but also this modal's entrance animation settling into its
    // final size after the fit() above ran against a mid-transition
    // snapshot. Left stale, xterm keeps the narrower column count from that
    // snapshot even once the box is visibly wider, so long lines (and the
    // cursor on them) render past the edge of the terminal.
    const resizeObserver = new ResizeObserver(() => sendResize());
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      dataDisposable.dispose();
      socket.close();
      term.dispose();
    };
  }, [node.id]);

  // Portalled to document.body like every other modal here: `.animate-fade-in`
  // sets `transform: translateZ(0)`, which makes any ancestor carrying it the
  // containing block for `position: fixed` — rendered in place, the overlay
  // gets trapped inside the tab's content area instead of covering the page.
  return createPortal(
    <div className="fixed inset-0 bg-zinc-950/85 backdrop-blur-sm z-50 flex items-center justify-center p-4 animate-fade-in">
      <div className="bg-zinc-950 border border-zinc-800 rounded-2xl shadow-2xl w-full max-w-4xl flex flex-col overflow-hidden animate-modal-in">
        <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800">
          <div className="flex flex-col">
            <span className="text-sm font-bold text-zinc-200">{node.hostname}</span>
            <span className={`text-[11px] ${status === 'failed' ? 'text-red-400' : 'text-zinc-500'}`}>
              {status === 'connecting' && t('terminalConnecting')}
              {status === 'open' && t('terminalConnected')}
              {status === 'failed' && t('terminalConnectFailed')}
              {status === 'closed' &&
                t(closeReason && CLOSE_REASON_KEYS[closeReason] ? CLOSE_REASON_KEYS[closeReason] : 'terminalClosed')}
            </span>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 text-zinc-500 hover:text-zinc-200 transition-colors cursor-pointer"
          >
            <X size={18} />
          </button>
        </div>
        {/* fitAddon measures containerRef's clientHeight/clientWidth, which
            includes padding -- but xterm's own child elements only get the
            padding-excluded content box. Padding directly on the measured
            element makes fit() think there's room for one more row/column
            than actually renders, so the last row (where the cursor usually
            sits) overflows below the visible box. Padding lives on this
            outer, unmeasured wrapper instead. */}
        <div className="p-2 bg-black overflow-hidden" style={{ height: '60vh' }}>
          <div ref={containerRef} className="w-full h-full" />
        </div>
      </div>
    </div>,
    document.body,
  );
}
