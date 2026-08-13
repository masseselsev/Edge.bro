import { Server, HardDrive, History, Settings as Gear, Terminal, Cpu, Calendar } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

export type Tab = 'fleet' | 'flasher' | 'history' | 'logs' | 'settings' | 'clientiso' | 'schedule';

export interface TabDefinition {
  id: Tab;
  icon: LucideIcon;
  /** Key into the translation table; the fallback is only for missing keys. */
  labelKey: string;
  labelFallback?: string;
  /** Kiosk mode hides the fleet-management half of the app. */
  kioskVisible: boolean;
  /** The Live-CD tab is styled as a call to action rather than a peer. */
  accent?: boolean;
}

/**
 * The tab bar, in display order.
 *
 * Previously five near-identical `<button>` blocks with the active/inactive
 * class ternary written out each time, three of them wrapped in their own
 * `{!isKiosk && ...}`. Adding a tab meant copying forty lines and remembering
 * which of them to change.
 */
export const TABS: TabDefinition[] = [
  { id: 'fleet',    icon: Server,    labelKey: 'tabFleet',        kioskVisible: false },
  { id: 'schedule', icon: Calendar,  labelKey: 'tabSchedule',     kioskVisible: false },
  { id: 'flasher',  icon: HardDrive, labelKey: 'tabFlasher',      kioskVisible: true },
  { id: 'history',  icon: History,   labelKey: 'tabHistory',      kioskVisible: true },
  { id: 'logs',     icon: Terminal,  labelKey: 'tabLogs',         kioskVisible: true },
  { id: 'settings', icon: Gear,      labelKey: 'tabSettings',     kioskVisible: false },
  {
    id: 'clientiso',
    icon: Cpu,
    labelKey: 'tabLiveCdKiosks',
    labelFallback: 'Live-CD & Kiosks',
    kioskVisible: false,
    accent: true,
  },
];

const TAB_IDS = new Set<string>(TABS.map(tab => tab.id));

/** The tab to open on load: whatever was last used, if it is still a real tab. */
export function restoreActiveTab(saved: string | null): Tab {
  return saved && TAB_IDS.has(saved) ? (saved as Tab) : 'fleet';
}
