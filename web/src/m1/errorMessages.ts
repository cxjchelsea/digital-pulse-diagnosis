import type {M1ApiError} from './types';

/** 稳定错误码 → 工程可读文案（不暴露路径/堆栈）。 */
const CODE_LABELS: Record<string, string> = {
  session_not_found: '会话不存在',
  run_not_found: 'Run 不存在',
  analysis_not_available: '分析尚不可用',
  invalid_manifest: '会话清单无效',
  artifact_corrupted: '工件已损坏',
  semantic_linkage_mismatch: '分析与 SP 结果语义不一致',
  artifact_conflict: '该 run_id 已存在，请使用新的审计 run_id。',
  invalid_request: '请求参数无效',
  invalid_session_id: '会话标识无效',
  invalid_run_id: 'Run 标识无效',
  replay_failed: '重放失败',
  sp_processing_failed: 'SP 处理失败',
  internal_error: '内部错误',
  unknown_error: '未知错误',
};

export function describeM1ApiError(error: M1ApiError): string {
  const mapped = CODE_LABELS[error.code];
  if (error.code === 'artifact_conflict') {
    return CODE_LABELS.artifact_conflict;
  }
  // invalid_request 展示调用方给出的工程说明（例如 provenance 拒绝原因）
  if (error.code === 'invalid_request' && error.message.trim()) {
    return error.message.trim();
  }
  if (mapped) {
    return `${mapped}（${error.code}）`;
  }
  return `请求失败（${error.code}）`;
}

export function isAnalysisUnavailable(error: M1ApiError | null): boolean {
  return error?.code === 'analysis_not_available';
}
