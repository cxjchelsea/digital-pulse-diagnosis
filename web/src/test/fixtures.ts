import type {
  AnalysisResponse,
  ChannelsResponse,
  ReplayResponse,
  RunDetail,
  RunsResponse,
  SessionDetail,
  SessionsResponse,
} from '../m1/types';

export const fixtureSessions: SessionsResponse = {
  api_version: 'm1-p3c-api-v1',
  sessions: [
    {
      api_version: 'm1-p3c-api-v1',
      session_id: 'session-a',
      source_type: 'simulator',
      completed: true,
      completion_reason: 'completed_normally',
      raw_persistence_status: 'committed',
      app_registered: true,
      committed_run_count: 1,
      current_run_id: 'run-a1',
    },
    {
      api_version: 'm1-p3c-api-v1',
      session_id: 'session-b',
      source_type: 'simulator',
      completed: true,
      completion_reason: 'completed_normally',
      raw_persistence_status: 'committed',
      app_registered: false,
      committed_run_count: 0,
      current_run_id: null,
    },
  ],
};

export function makeSessionDetail(sessionId: string): SessionDetail {
  return {
    api_version: 'm1-p3c-api-v1',
    session_id: sessionId,
    source_type: 'simulator',
    completed: true,
    completion_reason: 'completed_normally',
    raw_persistence_status: 'committed',
    app_registered: true,
    committed_run_count: 1,
    current_run_id: 'run-a1',
    sample_rate_hz: 250,
    configured_channels: ['pulse', 'load', 'ppg'],
    started_at_utc: '2026-01-01T00:00:00Z',
    ended_at_utc: '2026-01-01T00:00:08Z',
    parameter_status: 'simulation_only',
    formal_parameters: null,
    formal_parameters_allowed: false,
    limitations: ['synthetic_only', 'pending_h1_calibration'],
    raw_integrity_assurance: 'crc_verified',
    source_assets: [],
    runs: [
      {
        run_id: 'run-a1',
        state: 'committed',
        committed_at_utc: '2026-01-01T00:01:00Z',
        execution_mode: 'replay',
        asset_roles: ['sp_result', 'app_analysis'],
      },
    ],
  };
}

export function makeChannels(
  sessionId: string,
  runId: string | null,
): ChannelsResponse {
  const raw = {
    pulse: {
      name: 'pulse',
      values: [1, 2, 3, 4],
      metadata: {
        original_count: 100,
        returned_count: 4,
        downsampled: true,
        downsampling: 'display-only',
      },
    },
    load: {
      name: 'load',
      values: [10, 11, 12, 13],
      metadata: {
        original_count: 100,
        returned_count: 4,
        downsampled: true,
        downsampling: 'display-only',
      },
    },
    ppg: {
      name: 'ppg',
      values: [0.1, 0.2, 0.3, 0.4],
      metadata: {
        original_count: 100,
        returned_count: 4,
        downsampled: true,
        downsampling: 'display-only',
      },
    },
    timestamps: {
      name: 'timestamps',
      values: [0, 1, 2, 3],
      metadata: {
        original_count: 100,
        returned_count: 4,
        downsampled: true,
        downsampling: 'display-only',
      },
    },
  };
  return {
    api_version: 'm1-p3c-api-v1',
    session_id: sessionId,
    run_id: runId,
    raw,
    processed: runId
      ? {
          'sp/series/pulse_filtered.bin': {
            name: 'pulse_filtered',
            values: [1.1, 2.1, 3.1, 4.1],
            metadata: {
              original_count: 100,
              returned_count: 4,
              downsampled: true,
              downsampling: 'display-only',
            },
          },
        }
      : {},
  };
}

export function makeBlockedAnalysis(sessionId: string, runId: string): AnalysisResponse {
  return {
    api_version: 'm1-p3c-api-v1',
    session_id: sessionId,
    run_id: runId,
    analysis: {
      schema_version: 'm1-p3b-app-analysis-v1',
      formal_parameters: null,
      limitations: ['synthetic_only', 'pending_h1_calibration'],
      quality: {
        window_id: 'win-1',
        label: 'blocked',
        reason_codes: ['weak_signal'],
        score: null,
        confidence: null,
        valid_duration_s: 1.2,
        parameter_status: 'simulation_only',
        metrics: {
          valid_fraction: 0.4,
          clipping_fraction: 0,
          baseline_drift_raw: null,
          pulse_std_raw: 1.2,
          beat_count: 3,
          ppg_match_rate: null,
        },
      },
      gate: {
        analysis_allowed: true,
        formal_parameters_allowed: false,
        blocking_codes: ['quality_blocked'],
        limitations: ['synthetic_only', 'pending_h1_calibration'],
        gate_version: 'm1-p3b-analysis-gate-v1',
      },
      integrity_summary: {
        sample_count: 2000,
        crc_error_count: 0,
        sequence_error_count: 0,
        missing_frame_count: 0,
        timestamp_error_count: 0,
        sensor_disconnection_count: 0,
        raw_persistence_status: 'committed',
        integrity_ok: true,
        pre_quality_blocked: false,
        blocking_codes: [],
        consistency: 'ok',
      },
      stable_windows: [
        {
          window_id: 'win-1',
          start_device_time_us: 0,
          end_device_time_us: 1000000,
          sample_count: 250,
          duration_s: 1,
        },
      ],
      raw_quality_metrics: {
        'win-1': {
          valid_fraction: 0.4,
          clipping_fraction: 0,
          baseline_drift_raw: null,
          pulse_std_raw: 1.2,
          beat_count: 3,
          ppg_match_rate: null,
        },
      },
      beat_summary: {
        'win-1': {
          beat_count: 3,
          interval_mean_ms: 800,
          interval_std_ms: 20,
          interval_cv: 0.025,
          detection_source: 'pulse',
        },
      },
      reference_summary: {
        'win-1': {
          pulse_beat_count: 3,
          ppg_beat_count: 0,
          matched_count: null,
          match_rate: null,
          median_lag_ms: null,
          lag_mad_ms: null,
          reference_available: false,
        },
      },
      filter_view_summary: {
        'win-1': {
          causal: {
            mode: 'causal',
            sample_count: 250,
            valid_count: 240,
            group_delay_samples: 8,
            filter_version: 'fir-v1',
            num_taps: 17,
          },
        },
      },
      engineering_unit_conversion: {
        converter_name: 'identity',
        converter_version: '0.1.0',
        parameter_status: 'simulation_only',
        raw_identity: 'raw_au',
        engineering_units_applied: false,
        conversion_status: 'passthrough',
        simulation_only: true,
        real_calibration_pending: true,
      },
      provenance: {
        app_processing_version: '0.1.0-p3b',
        app_manifest_schema_version: 'm1-app-manifest-v1',
        app_execution_mode: 'replay',
        app_software_commit_sha: 'a'.repeat(40),
        sp_processing_version: '0.4.0-p2',
        sp_parameter_version: 'p2-frozen',
        sp_parameter_digest: 'b'.repeat(64),
        sp_semantic_fingerprint_version: 'sp-result-fingerprint:v2',
        sp_result_sha256: 'c'.repeat(64),
      },
      semantic_fingerprint_version: 'app-analysis-fingerprint:v1',
      semantic_fingerprint_sha256: 'd'.repeat(64),
    },
  };
}

export function makeRuns(sessionId: string): RunsResponse {
  return {
    api_version: 'm1-p3c-api-v1',
    session_id: sessionId,
    current_run_id: 'run-a1',
    runs: [
      {
        run_id: 'run-a1',
        state: 'committed',
        committed_at_utc: '2026-01-01T00:01:00Z',
        execution_mode: 'replay',
        asset_roles: ['sp_result', 'app_analysis'],
      },
      {
        run_id: 'run-a2',
        state: 'committed',
        committed_at_utc: '2026-01-01T00:02:00Z',
        execution_mode: 'replay',
        asset_roles: ['sp_result', 'app_analysis'],
      },
    ],
  };
}

export function makeRunDetail(sessionId: string, runId: string): RunDetail {
  return {
    api_version: 'm1-p3c-api-v1',
    session_id: sessionId,
    run_id: runId,
    run: {
      run_id: runId,
      state: 'committed',
      committed_at_utc: '2026-01-01T00:01:00Z',
      execution_mode: 'replay',
      asset_roles: ['sp_result', 'app_analysis'],
    },
    assets: [
      {
        role: 'app_analysis',
        relative_path: 'app/runs/run-a1/analysis.json',
        sha256: 'e'.repeat(64),
        size_bytes: 128,
      },
    ],
  };
}

export function makeReplayResponse(sessionId: string, persisted: boolean): ReplayResponse {
  const analysis = makeBlockedAnalysis(sessionId, persisted ? 'run-new' : 'run-a1').analysis;
  return {
    api_version: 'm1-p3c-api-v1',
    session_id: sessionId,
    run_id: persisted ? 'run-new' : null,
    persisted,
    sp_result_sha256: 'f'.repeat(64),
    analysis,
  };
}

export function apiErrorEnvelope(code: string, message: string) {
  return {detail: {error: {code, message}}};
}
