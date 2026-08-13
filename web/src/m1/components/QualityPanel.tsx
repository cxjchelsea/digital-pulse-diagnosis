import React from 'react';
import {formatNullableNumber, joinCodes} from '../format';
import type {AppAnalysis} from '../types';
import {EmptyState, LoadingState} from './EmptyState';

export function QualityPanel({
  analysis,
  loading,
  errorText,
  unavailable,
}: {
  analysis: AppAnalysis | null;
  loading: boolean;
  errorText: string | null;
  unavailable: boolean;
}) {
  if (loading) {
    return <LoadingState label="正在加载质量分析结果…" />;
  }
  if (unavailable) {
    return (
      <div className="m1Unavailable" data-testid="m1-analysis-unavailable" role="status">
        <h3>分析尚不可用</h3>
        <p>后端返回 analysis_not_available。这不是质量阻断。</p>
      </div>
    );
  }
  if (errorText) {
    return (
      <div className="m1ErrorBox" role="alert">
        <h3>分析加载失败</h3>
        <p>{errorText}</p>
      </div>
    );
  }
  if (!analysis) {
    return <EmptyState title="尚无分析" detail="选择已提交 Run 后加载 AppAnalysis。" />;
  }

  const quality = analysis.quality;
  const gate = analysis.gate;
  const blockedByQuality =
    quality &&
    typeof quality.label === 'string' &&
    /block|fail|reject/i.test(quality.label);

  return (
    <section className="m1Panel" data-testid="m1-quality-panel">
      <div className="sectionHeading">
        <div>
          <h2>质量 / Gate</h2>
          <p>质量阻断是有效分析结果，不是 HTTP 失败。</p>
        </div>
      </div>
      {blockedByQuality ? (
        <div className="m1StatusChip blocked" data-testid="m1-quality-blocked">
          质量阻断（分析已加载）
        </div>
      ) : (
        <div className="m1StatusChip">质量结果已加载</div>
      )}
      <div className="metrics m1Metrics">
        <article>
          <small>质量标签</small>
          <strong>{quality?.label ?? '不可用'}</strong>
        </article>
        <article>
          <small>reason_codes</small>
          <strong>{joinCodes(quality?.reason_codes)}</strong>
        </article>
        <article>
          <small>score</small>
          <strong>{formatNullableNumber(quality?.score)}</strong>
        </article>
        <article>
          <small>confidence</small>
          <strong>{formatNullableNumber(quality?.confidence)}</strong>
        </article>
        <article>
          <small>valid_duration_s</small>
          <strong>{formatNullableNumber(quality?.valid_duration_s)}</strong>
        </article>
        <article>
          <small>parameter_status</small>
          <strong>{quality?.parameter_status ?? '不可用'}</strong>
        </article>
        <article>
          <small>分析可用</small>
          <strong>{gate?.analysis_allowed === true ? '是' : gate?.analysis_allowed === false ? '否' : '不可用'}</strong>
        </article>
        <article>
          <small>正式参数允许</small>
          <strong>
            {gate?.formal_parameters_allowed === true
              ? '是'
              : gate?.formal_parameters_allowed === false
                ? '否'
                : '不可用'}
          </strong>
        </article>
      </div>
      <p>Gate blocking_codes：{joinCodes(gate?.blocking_codes)}</p>
      <p>Gate limitations：{joinCodes(gate?.limitations)}</p>
      <p>
        formal_parameters = {analysis.formal_parameters === null ? 'null' : String(analysis.formal_parameters)}
      </p>
      {quality?.metrics ? (
        <div>
          <h3>质量 metrics</h3>
          <ul className="m1KeyList">
            {Object.entries(quality.metrics).map(([key, value]) => (
              <li key={key}>
                {key}: {formatNullableNumber(value)}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p>metrics：不可用</p>
      )}
    </section>
  );
}

export function IntegrityPanel({analysis}: {analysis: AppAnalysis | null}) {
  if (!analysis?.integrity_summary) {
    return <EmptyState title="完整性摘要不可用" />;
  }
  const integrity = analysis.integrity_summary;
  return (
    <section className="m1Panel" data-testid="m1-integrity-panel">
      <h2>完整性</h2>
      <p>
        完整性失败与质量失败需区分。integrity_ok=
        {integrity.integrity_ok === true ? '是' : integrity.integrity_ok === false ? '否' : '不可用'}
        ；pre_quality_blocked=
        {integrity.pre_quality_blocked === true
          ? '是'
          : integrity.pre_quality_blocked === false
            ? '否'
            : '不可用'}
      </p>
      <div className="metrics m1Metrics">
        <article>
          <small>sample_count</small>
          <strong>{formatNullableNumber(integrity.sample_count, 0)}</strong>
        </article>
        <article>
          <small>crc_error_count</small>
          <strong>{formatNullableNumber(integrity.crc_error_count, 0)}</strong>
        </article>
        <article>
          <small>sequence_error_count</small>
          <strong>{formatNullableNumber(integrity.sequence_error_count, 0)}</strong>
        </article>
        <article>
          <small>missing_frame_count</small>
          <strong>{formatNullableNumber(integrity.missing_frame_count, 0)}</strong>
        </article>
        <article>
          <small>timestamp_error_count</small>
          <strong>{formatNullableNumber(integrity.timestamp_error_count, 0)}</strong>
        </article>
        <article>
          <small>sensor_disconnection_count</small>
          <strong>{formatNullableNumber(integrity.sensor_disconnection_count, 0)}</strong>
        </article>
        <article>
          <small>raw_persistence_status</small>
          <strong>{integrity.raw_persistence_status ?? '不可用'}</strong>
        </article>
        <article>
          <small>consistency</small>
          <strong>{integrity.consistency ?? '不可用'}</strong>
        </article>
      </div>
      <p>blocking_codes：{joinCodes(integrity.blocking_codes)}</p>
    </section>
  );
}
