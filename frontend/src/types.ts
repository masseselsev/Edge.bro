/**
 * The shapes the API actually returns, in one place.
 *
 * `Node` was declared five times across the components, `BackupHistory` three
 * times and `TaskLog` three times — each a hand-copied subset of the server
 * model, each frozen at whatever the server returned on the day it was written.
 * They had already drifted: only two of the five `Node`s knew about
 * `next_retry_at`, and none of them carried `repo_size_bytes`, so the field the
 * fleet list renders was reaching the UI as `any`.
 *
 * These mirror `backend/schemas/`. They are written by hand rather than
 * generated, because a generated file that nothing regenerates drifts exactly
 * as quietly as a copied one. What keeps them honest is
 * `backend/tests/test_frontend_types.py`, which compares every field here
 * against the OpenAPI document FastAPI emits and fails when the two disagree.
 *
 * Optional-vs-required follows the server: a field the server declares
 * `Optional[X] = None` is `X | null` if it always appears in the response, and
 * `X | null | undefined` (written `?:`) only where the server may omit the key
 * entirely.
 */

/** `schemas.NodeResponse` — the full node, as returned by /api/nodes. */
export interface Node {
  id: number;
  hostname: string;
  ip_address: string;
  ssh_port: number;
  status: string;
  last_backup: string | null;
  disk_type: string;
  network_iface: string | null;
  efi_uuid: string | null;
  partition_layout: any[] | null;
  os_version: string | null;
  next_retry_at: string | null;
  /** Bytes this node occupies in the shared repository. Cached server-side. */
  repo_size_bytes: number | null;

  group_id: number | null;
  backup_paused: boolean;
  backup_today: boolean;
  missed_window: boolean;

  cpu_info: string | null;
  memory_info: string | null;
  edge_version: string | null;
  notes: string | null;
  hasp_runtime_version: string | null;

  is_backup_running?: boolean;
  backup_progress?: number;
  backup_task_id?: string | null;
  last_ping_status?: boolean | null;
  last_available_at?: string | null;

  /** null = inherit from the node's group, then the global setting. */
  orchestrator_behind_nat?: boolean | null;
  /** KiB/s. null = inherit the group limit, then unlimited. */
  upload_rate_limit?: number | null;
}

/** `schemas.BackupHistoryResponse`. */
export interface BackupHistory {
  id: number;
  node_id: number;
  archive_name: string;
  timestamp: string;
  original_size: number;
  deduplicated_size: number;
  status: string;
  /** Omitted by the list endpoints, which defer the column. */
  log_output?: string | null;
  comment: string | null;
  /** Transfer throughput to the repository, Mbit/s. null for older rows. */
  avg_speed_mbps: number | null;
  max_speed_mbps: number | null;
  duration_seconds: number | null;
  error_category: string | null;
}

/** `schemas.TaskLogResponse`. */
export interface TaskLog {
  id: string;
  task_type: string;
  status: string;
  node_id: number | null;
  created_at: string;
  updated_at: string;
  log_output: string;
  /** Set when the caller passed ?since=; see the console's incremental fetch. */
  log_offset: number;
  log_length: number | null;
}

/** `schemas.TaskLogSummaryResponse` — the list view, without the log body. */
export interface TaskLogSummary {
  id: string;
  task_type: string;
  status: string;
  node_id: number | null;
  created_at: string;
  updated_at: string;
}

/** `schemas.BackupGroupResponse`. */
export interface BackupGroup {
  id: number;
  name: string;
  /** weekly, monthly, quarterly, yearly. */
  interval: string;
  /** Which week of the period a monthly-or-longer group runs in. */
  target_week: number;
  start_time: string;
  end_time: string;
  concurrency_limit: number;
  /** Spreads a group's nodes across the period instead of all on one day. */
  randomize_days: boolean;
  timezone: string;
  /** false = use the global retention policy and ignore retention_policy. */
  override_retention: boolean;
  retention_policy: RetentionPolicy | null;
  /** null = inherit the global setting. */
  orchestrator_behind_nat: number | boolean | null;
  /** KiB/s. null = unlimited. */
  upload_rate_limit: number | null;
  /** e.g. "zstd:3". null = global default. */
  compression: string | null;
  /** Seconds. null = auto-calculate. */
  checkpoint_interval: number | null;
  /** Percent of one core. null = no limit. */
  cpu_quota: number | null;
}

/** `schemas.RetentionPolicySchema`. */
export interface RetentionPolicy {
  /** "interval", "count" or "timeframe" — decides which fields below apply. */
  type: string;
  keep_daily: number;
  keep_weekly: number;
  keep_monthly: number;
  keep_last: number;
  /** With within_unit, the "timeframe" mode's window. */
  within_value: number;
  /** "d", "w", "m" or "y". */
  within_unit: string;
}

/** `schemas.ExclusionSchema`. */
export interface Exclusion {
  pattern: string;
  comment: string;
}
