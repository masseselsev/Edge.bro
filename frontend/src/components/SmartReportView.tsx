import React, { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { Field, formatBytes } from './NodeHealthModal';

/**
 * Pretty-printed view of a raw smartctl -j report.
 *
 * The report shape varies by protocol (ATA vs NVMe vs SCSI) and by vendor, so
 * this does not assume a fixed schema. It pulls out the fields worth a
 * dedicated layout — device summary, the ATA attribute table, the NVMe health
 * log — and falls back to a generic collapsible tree for everything else, so
 * nothing from the original report is hidden, only organised.
 */

const NVME_DATA_UNIT_BYTES = 512_000;

const CONSUMED_TOP_LEVEL_KEYS = new Set([
  'device',
  'model_name',
  'model_family',
  'serial_number',
  'firmware_version',
  'user_capacity',
  'rotation_rate',
  'smart_status',
  'ata_smart_attributes',
  'nvme_smart_health_information_log',
]);

function humanizeKey(key: string): string {
  return key
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function JsonPrimitive({ value }: { value: any }) {
  if (value === null || value === undefined || value === '') {
    return <span className="text-zinc-600 italic">—</span>;
  }
  if (typeof value === 'boolean') {
    return <span className={value ? 'text-emerald-400' : 'text-zinc-500'}>{String(value)}</span>;
  }
  if (typeof value === 'number') {
    return <span className="text-amber-300 tabular-nums">{value.toLocaleString()}</span>;
  }
  return <span className="text-sky-300 break-all">{String(value)}</span>;
}

function JsonNode({ label, value, depth }: { label: string; value: any; depth: number }) {
  const [open, setOpen] = useState(depth < 1);
  const isObject = value !== null && typeof value === 'object';

  if (!isObject) {
    return (
      <div className="flex items-baseline gap-2 py-0.5 text-xs" style={{ paddingLeft: depth * 14 }}>
        <span className="text-zinc-500 shrink-0">{humanizeKey(label)}</span>
        <span className="flex-1 border-b border-dotted border-zinc-800/60" />
        <JsonPrimitive value={value} />
      </div>
    );
  }

  const entries = Array.isArray(value)
    ? value.map((v, i) => [String(i), v] as [string, any])
    : Object.entries(value);

  if (entries.length === 0) {
    return (
      <div className="flex items-baseline gap-2 py-0.5 text-xs" style={{ paddingLeft: depth * 14 }}>
        <span className="text-zinc-500">{humanizeKey(label)}</span>
        <span className="text-zinc-600 italic">{Array.isArray(value) ? '[]' : '{}'}</span>
      </div>
    );
  }

  return (
    <div style={{ paddingLeft: depth * 14 }}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 py-0.5 text-xs text-zinc-400 hover:text-zinc-200 w-full text-left cursor-pointer"
      >
        {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
        <span className="font-semibold text-zinc-300">{humanizeKey(label)}</span>
        <span className="text-zinc-600">{Array.isArray(value) ? `[${entries.length}]` : `{${entries.length}}`}</span>
      </button>
      {open && (
        <div>
          {entries.map(([k, v]) => (
            <JsonNode key={k} label={k} value={v} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}

interface SmartReportViewProps {
  report: any;
  t: (key: string, vars?: Record<string, any>) => string;
}

export default function SmartReportView({ report, t }: SmartReportViewProps) {
  const [showRawJson, setShowRawJson] = useState(false);

  if (!report || typeof report !== 'object') {
    return <p className="text-xs text-zinc-500 italic">{t('healthNoSmart')}</p>;
  }

  if (report.error) {
    return <p className="text-xs text-rose-400">{String(report.error)}</p>;
  }

  const device = report.device || {};
  const attrTable: any[] | undefined = report.ata_smart_attributes?.table;
  const nvmeLog: Record<string, any> | undefined = report.nvme_smart_health_information_log;
  const statusPassed: boolean | undefined = report.smart_status?.passed;
  const capacityBytes: number | undefined = report.user_capacity?.bytes;

  const otherEntries = Object.entries(report).filter(([k]) => !CONSUMED_TOP_LEVEL_KEYS.has(k));

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 p-3 bg-zinc-950/50 border border-zinc-800/80 rounded-xl">
        <Field label={t('healthReportModel')} value={report.model_name || '—'} />
        <Field label={t('healthReportSerial')} value={report.serial_number || '—'} />
        <Field label={t('healthReportFirmware')} value={report.firmware_version || '—'} />
        <Field
          label={t('healthReportDevice')}
          value={`${device.name || '—'}${device.protocol || device.type ? ` (${device.protocol || device.type})` : ''}`}
        />
        {capacityBytes !== undefined && (
          <Field label={t('healthReportCapacity')} value={formatBytes(capacityBytes)} />
        )}
        {report.rotation_rate !== undefined && (
          <Field
            label={t('healthReportMedia')}
            value={report.rotation_rate === 0 ? t('healthReportSsd') : `${report.rotation_rate} RPM`}
          />
        )}
        {statusPassed !== undefined && statusPassed !== null && (
          <Field
            label={t('healthReportSmartStatus')}
            value={statusPassed ? t('healthReportPassed') : t('healthReportFailed')}
            tone={statusPassed ? 'text-emerald-400' : 'text-rose-400'}
          />
        )}
      </div>

      {Array.isArray(attrTable) && attrTable.length > 0 && (
        <div className="border border-zinc-800/80 rounded-xl overflow-hidden">
          <p className="px-3 py-2 text-[11px] font-bold text-zinc-300 bg-zinc-950/60 border-b border-zinc-800/80">
            {t('healthReportAttributesTitle')}
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-[11px] text-left">
              <thead>
                <tr className="text-zinc-500 border-b border-zinc-800/80">
                  <th className="px-2 py-1.5 font-semibold">ID</th>
                  <th className="px-2 py-1.5 font-semibold">{t('healthReportAttrName')}</th>
                  <th className="px-2 py-1.5 font-semibold text-right">{t('healthReportAttrValue')}</th>
                  <th className="px-2 py-1.5 font-semibold text-right">{t('healthReportAttrWorst')}</th>
                  <th className="px-2 py-1.5 font-semibold text-right">{t('healthReportAttrThresh')}</th>
                  <th className="px-2 py-1.5 font-semibold text-right">{t('healthReportAttrRaw')}</th>
                </tr>
              </thead>
              <tbody>
                {[...attrTable]
                  .sort((a, b) => (a.id ?? 0) - (b.id ?? 0))
                  .map((attr) => {
                    const failing = !!attr.when_failed;
                    return (
                      <tr key={attr.id} className={`border-b border-zinc-800/40 ${failing ? 'bg-rose-500/10' : ''}`}>
                        <td className="px-2 py-1 text-zinc-500 tabular-nums">{attr.id}</td>
                        <td className={`px-2 py-1 font-medium ${failing ? 'text-rose-400' : 'text-zinc-200'}`}>
                          {attr.name}
                          {attr.flags?.prefailure && (
                            <span className="ml-1.5 px-1 rounded text-[9px] uppercase font-bold bg-amber-500/10 text-amber-400 align-middle">
                              {t('healthReportPrefail')}
                            </span>
                          )}
                        </td>
                        <td className="px-2 py-1 text-right tabular-nums text-zinc-300">{attr.value}</td>
                        <td className="px-2 py-1 text-right tabular-nums text-zinc-500">{attr.worst}</td>
                        <td className="px-2 py-1 text-right tabular-nums text-zinc-500">{attr.thresh}</td>
                        <td className="px-2 py-1 text-right tabular-nums text-zinc-300">
                          {attr.raw?.string ?? attr.raw?.value ?? '—'}
                        </td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {nvmeLog && (
        <div className="border border-zinc-800/80 rounded-xl overflow-hidden">
          <p className="px-3 py-2 text-[11px] font-bold text-zinc-300 bg-zinc-950/60 border-b border-zinc-800/80">
            {t('healthReportNvmeTitle')}
          </p>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 p-3">
            {Object.entries(nvmeLog).map(([key, value]) => {
              if ((key === 'data_units_written' || key === 'data_units_read') && typeof value === 'number') {
                return <Field key={key} label={humanizeKey(key)} value={formatBytes(value * NVME_DATA_UNIT_BYTES)} />;
              }
              return (
                <Field
                  key={key}
                  label={humanizeKey(key)}
                  value={typeof value === 'number' ? value.toLocaleString() : String(value)}
                />
              );
            })}
          </div>
        </div>
      )}

      {otherEntries.length > 0 && (
        <div className="border border-zinc-800/80 rounded-xl overflow-hidden">
          <p className="px-3 py-2 text-[11px] font-bold text-zinc-300 bg-zinc-950/60 border-b border-zinc-800/80">
            {t('healthReportOtherTitle')}
          </p>
          <div className="p-2 max-h-72 overflow-y-auto">
            {otherEntries.map(([k, v]) => (
              <JsonNode key={k} label={k} value={v} depth={0} />
            ))}
          </div>
        </div>
      )}

      <button
        type="button"
        onClick={() => setShowRawJson((v) => !v)}
        className="text-[10px] text-zinc-500 hover:text-zinc-300 underline cursor-pointer"
      >
        {showRawJson ? t('healthHideRawJson') : t('healthShowRawJson')}
      </button>
      {showRawJson && (
        <pre className="p-3 bg-zinc-950 border border-zinc-800 rounded-lg text-[10px] text-zinc-400 overflow-auto max-h-72">
          {JSON.stringify(report, null, 2)}
        </pre>
      )}
    </div>
  );
}
