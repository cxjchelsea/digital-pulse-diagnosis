import React from 'react';
import {formatNullableNumber} from '../format';
import type {AppAnalysis} from '../types';
import {EmptyState} from './EmptyState';

export function BeatReferencePanel({analysis}: {analysis: AppAnalysis | null}) {
  if (!analysis) {
    return <EmptyState title="尚无拍/参考摘要" />;
  }

  const windows = analysis.stable_windows ?? [];
  const beatSummary = analysis.beat_summary ?? {};
  const referenceSummary = analysis.reference_summary ?? {};
  const rawMetrics = analysis.raw_quality_metrics ?? {};
  const filterSummary = analysis.filter_view_summary ?? {};
  const conversion = analysis.engineering_unit_conversion;

  return (
    <>
      <section className="m1Panel" data-testid="m1-stable-windows">
        <h2>稳态窗口 / 质量时间线</h2>
        {windows.length === 0 ? (
          <EmptyState title="无稳态窗口" />
        ) : (
          <div className="timeline">
            {windows.map((windowItem) => {
              const metrics = rawMetrics[windowItem.window_id];
              return (
                <article key={windowItem.window_id}>
                  <div className="tick">{windowItem.window_id}</div>
                  <div>
                    <h3>
                      {windowItem.start_device_time_us} → {windowItem.end_device_time_us}
                    </h3>
                    <p>
                      duration_s={formatNullableNumber(windowItem.duration_s)} · samples=
                      {windowItem.sample_count}
                    </p>
                    {metrics ? (
                      <small>
                        valid_fraction={formatNullableNumber(metrics.valid_fraction)} ·
                        clipping_fraction={formatNullableNumber(metrics.clipping_fraction)} ·
                        baseline_drift_raw={formatNullableNumber(metrics.baseline_drift_raw)} ·
                        pulse_std_raw={formatNullableNumber(metrics.pulse_std_raw)} · beat_count=
                        {formatNullableNumber(metrics.beat_count, 0)} · ppg_match_rate=
                        {formatNullableNumber(metrics.ppg_match_rate)}
                      </small>
                    ) : (
                      <small>该窗口无 raw_quality_metrics</small>
                    )}
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>

      <section className="m1Panel" data-testid="m1-beat-panel">
        <h2>拍摘要</h2>
        {Object.keys(beatSummary).length === 0 ? (
          <EmptyState title="无 beat_summary" />
        ) : (
          <ul className="m1KeyList">
            {Object.entries(beatSummary).map(([windowId, summary]) => (
              <li key={windowId}>
                <strong>{windowId}</strong> · beat_count=
                {formatNullableNumber(summary.beat_count, 0)} · interval_mean_ms=
                {formatNullableNumber(summary.interval_mean_ms)} · interval_std_ms=
                {formatNullableNumber(summary.interval_std_ms)} · interval_cv=
                {formatNullableNumber(summary.interval_cv)} · detection_source=
                {summary.detection_source ?? '不可用'}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="m1Panel" data-testid="m1-reference-panel">
        <h2>参考 / PPG 摘要</h2>
        {Object.keys(referenceSummary).length === 0 ? (
          <EmptyState title="无 reference_summary" />
        ) : (
          <ul className="m1KeyList">
            {Object.entries(referenceSummary).map(([windowId, summary]) => (
              <li key={windowId}>
                <strong>{windowId}</strong> · reference_available=
                {summary.reference_available === true
                  ? '是'
                  : summary.reference_available === false
                    ? '否（不可用，非 0% 匹配）'
                    : '不可用'}{' '}
                · pulse_beat_count={formatNullableNumber(summary.pulse_beat_count, 0)} ·
                ppg_beat_count={formatNullableNumber(summary.ppg_beat_count, 0)} · matched_count=
                {formatNullableNumber(summary.matched_count, 0)} · match_rate=
                {summary.reference_available === false
                  ? '不可用'
                  : formatNullableNumber(summary.match_rate)}{' '}
                · median_lag_ms={formatNullableNumber(summary.median_lag_ms)} · lag_mad_ms=
                {formatNullableNumber(summary.lag_mad_ms)}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="m1Panel" data-testid="m1-filter-panel">
        <h2>滤波视图元数据</h2>
        {Object.keys(filterSummary).length === 0 ? (
          <EmptyState title="无 filter_view_summary" />
        ) : (
          <ul className="m1KeyList">
            {Object.entries(filterSummary).map(([windowId, filters]) =>
              Object.entries(filters).map(([filterKey, summary]) => (
                <li key={`${windowId}-${filterKey}`}>
                  {windowId}/{filterKey} · mode={summary.mode ?? '不可用'} · sample_count=
                  {formatNullableNumber(summary.sample_count, 0)} · valid_count=
                  {formatNullableNumber(summary.valid_count, 0)} · group_delay_samples=
                  {formatNullableNumber(summary.group_delay_samples, 0)} · filter_version=
                  {summary.filter_version ?? '不可用'} · num_taps=
                  {formatNullableNumber(summary.num_taps, 0)}
                </li>
              )),
            )}
          </ul>
        )}
      </section>

      <section className="m1Panel" data-testid="m1-engineering-panel">
        <h2>工程单位换算状态</h2>
        {!conversion ? (
          <EmptyState title="无 engineering_unit_conversion" />
        ) : (
          <ul className="m1KeyList">
            <li>converter_name：{conversion.converter_name ?? '不可用'}</li>
            <li>converter_version：{conversion.converter_version ?? '不可用'}</li>
            <li>parameter_status：{conversion.parameter_status ?? '不可用'}</li>
            <li>raw_identity：{conversion.raw_identity ?? '不可用'}</li>
            <li>
              engineering_units_applied：
              {conversion.engineering_units_applied === true
                ? '是'
                : conversion.engineering_units_applied === false
                  ? '否'
                  : '不可用'}
            </li>
            <li>conversion_status：{conversion.conversion_status ?? '不可用'}</li>
            <li>
              simulation_only：
              {conversion.simulation_only === true
                ? '是'
                : conversion.simulation_only === false
                  ? '否'
                  : '不可用'}
            </li>
            <li data-testid="m1-real-calibration-pending">
              real_calibration_pending：
              {conversion.real_calibration_pending === true
                ? '是（等待 H1 校准）'
                : conversion.real_calibration_pending === false
                  ? '否'
                  : '不可用'}
            </li>
          </ul>
        )}
      </section>
    </>
  );
}
