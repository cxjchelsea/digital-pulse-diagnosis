/** P3C HTTP 契约类型：仅镜像后端字段，不在前端发明分析语义。 */

export type M1ApiErrorCode =
  | 'session_not_found'
  | 'run_not_found'
  | 'analysis_not_available'
  | 'invalid_manifest'
  | 'artifact_corrupted'
  | 'semantic_linkage_mismatch'
  | 'artifact_conflict'
  | 'invalid_request'
  | 'invalid_session_id'
  | 'invalid_run_id'
  | 'replay_failed'
  | 'sp_processing_failed'
  | 'internal_error'
  | 'unknown_error';

export interface M1ApiError {
  code: M1ApiErrorCode | string;
  message: string;
  httpStatus: number;
}

export interface SessionSummary {
  api_version: string;
  session_id: string;
  source_type: string;
  completed: boolean;
  completion_reason: string | null;
  raw_persistence_status: string;
  app_registered: boolean;
  committed_run_count: number;
  current_run_id: string | null;
}

export interface SessionsResponse {
  api_version: string;
  sessions: SessionSummary[];
}

export interface SafeAssetRef {
  role: string;
  relative_path: string;
  sha256: string;
  size_bytes: number;
  media_type?: string | null;
  producer?: string | null;
  version?: string | null;
  checksum_source?: string | null;
}

export interface RunSummary {
  run_id: string;
  state: string;
  committed_at_utc: string;
  execution_mode: string;
  asset_roles: string[];
}

export interface SessionDetail extends SessionSummary {
  sample_rate_hz: number;
  configured_channels: string[];
  started_at_utc: string;
  ended_at_utc: string | null;
  parameter_status: string;
  formal_parameters: null;
  formal_parameters_allowed: boolean;
  limitations: string[];
  raw_integrity_assurance: string | null;
  source_assets: SafeAssetRef[];
  runs: RunSummary[];
}

export interface SeriesMetadata {
  original_count: number;
  returned_count: number;
  downsampled: boolean;
  downsampling: string;
}

export interface ChannelSeries {
  name: string;
  values: Array<number | null | string>;
  metadata: SeriesMetadata;
}

export interface ChannelsResponse {
  api_version: string;
  session_id: string;
  run_id: string | null;
  raw: Record<string, ChannelSeries>;
  processed: Record<string, ChannelSeries>;
}

export interface QualityView {
  window_id?: string | null;
  label?: string | null;
  reason_codes?: string[] | null;
  score?: number | null;
  confidence?: number | null;
  valid_duration_s?: number | null;
  metrics?: Record<string, number | null> | null;
  parameter_status?: string | null;
}

export interface IntegritySummary {
  sample_count?: number | null;
  crc_error_count?: number | null;
  sequence_error_count?: number | null;
  missing_frame_count?: number | null;
  timestamp_error_count?: number | null;
  sensor_disconnection_count?: number | null;
  raw_persistence_status?: string | null;
  integrity_ok?: boolean | null;
  pre_quality_blocked?: boolean | null;
  blocking_codes?: string[] | null;
  consistency?: string | null;
}

export interface StableWindow {
  window_id: string;
  start_device_time_us: number;
  end_device_time_us: number;
  sample_count: number;
  duration_s: number;
}

export interface BeatSummary {
  beat_count?: number | null;
  interval_mean_ms?: number | null;
  interval_std_ms?: number | null;
  interval_cv?: number | null;
  detection_source?: string | null;
}

export interface ReferenceSummary {
  pulse_beat_count?: number | null;
  ppg_beat_count?: number | null;
  matched_count?: number | null;
  match_rate?: number | null;
  median_lag_ms?: number | null;
  lag_mad_ms?: number | null;
  reference_available?: boolean | null;
}

export interface FilterViewSummary {
  mode?: string | null;
  sample_count?: number | null;
  valid_count?: number | null;
  group_delay_samples?: number | null;
  filter_version?: string | null;
  num_taps?: number | null;
}

export interface EngineeringUnitConversion {
  converter_name?: string | null;
  converter_version?: string | null;
  parameter_status?: string | null;
  raw_identity?: string | null;
  engineering_units_applied?: boolean | null;
  conversion_status?: string | null;
  simulation_only?: boolean | null;
  real_calibration_pending?: boolean | null;
}

export interface GateDecision {
  analysis_allowed?: boolean | null;
  formal_parameters_allowed?: boolean | null;
  blocking_codes?: string[] | null;
  limitations?: string[] | null;
  gate_version?: string | null;
}

export interface Provenance {
  app_processing_version?: string | null;
  app_manifest_schema_version?: string | null;
  app_execution_mode?: string | null;
  app_software_commit_sha?: string | null;
  sp_processing_version?: string | null;
  sp_parameter_version?: string | null;
  sp_parameter_digest?: string | null;
  sp_semantic_fingerprint_version?: string | null;
  sp_result_sha256?: string | null;
}

export interface AppAnalysis {
  schema_version?: string;
  session?: Record<string, unknown>;
  processing_status?: string | null;
  quality?: QualityView | null;
  gate?: GateDecision | null;
  formal_parameters?: null;
  limitations?: string[] | null;
  integrity_summary?: IntegritySummary | null;
  stable_windows?: StableWindow[] | null;
  raw_quality_metrics?: Record<string, Record<string, number | null>> | null;
  filter_view_summary?: Record<string, Record<string, FilterViewSummary>> | null;
  beat_summary?: Record<string, BeatSummary> | null;
  reference_summary?: Record<string, ReferenceSummary> | null;
  engineering_unit_conversion?: EngineeringUnitConversion | null;
  provenance?: Provenance | null;
  semantic_fingerprint_version?: string | null;
  semantic_fingerprint_sha256?: string | null;
}

export interface AnalysisResponse {
  api_version: string;
  session_id: string;
  run_id: string;
  analysis: AppAnalysis;
}

export interface RunsResponse {
  api_version: string;
  session_id: string;
  current_run_id: string | null;
  runs: RunSummary[];
}

export interface RunDetail {
  api_version: string;
  session_id: string;
  run_id: string;
  run: RunSummary;
  assets: SafeAssetRef[];
}

export interface ReplayRequest {
  persist?: boolean;
  run_id?: string | null;
  software_commit_sha?: string;
}

export interface ReplayResponse {
  api_version: string;
  session_id: string;
  run_id: string | null;
  persisted: boolean;
  sp_result_sha256: string;
  analysis: AppAnalysis;
}

export type LoadState = 'idle' | 'loading' | 'success' | 'empty' | 'error';
