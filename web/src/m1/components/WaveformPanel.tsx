import React from 'react';
import {buildPolylinePoints} from '../chart';
import type {ChannelSeries, ChannelsResponse} from '../types';
import {EmptyState, LoadingState} from './EmptyState';

function SeriesChart({
  title,
  series,
  variant = 'raw',
}: {
  title: string;
  series: ChannelSeries;
  variant?: 'raw' | 'processed';
}) {
  const chart = buildPolylinePoints(series.values, 50);
  return (
    <div className="m1WaveBlock">
      <div className="m1WaveMeta">
        <h3>
          {title}
          {variant === 'processed' ? ' · processed / filter view' : ' · raw'}
        </h3>
        <p>
          original={series.metadata.original_count} · returned={series.metadata.returned_count} ·
          downsampled={String(series.metadata.downsampled)}
          {series.metadata.downsampled
            ? ` · ${series.metadata.downsampling || 'display-only'}（仅为显示表示）`
            : ''}
        </p>
        <p>{chart.readout}</p>
      </div>
      {chart.usable ? (
        <svg
          className={`chart${variant === 'processed' ? ' force' : ''}`}
          viewBox="0 0 100 50"
          preserveAspectRatio="none"
          role="img"
          aria-label={`${title} 波形`}
        >
          <polyline points={chart.points} />
        </svg>
      ) : (
        <p>无可绘制点</p>
      )}
    </div>
  );
}

export function WaveformPanel({
  channels,
  loading,
  errorText,
  selectedRunId,
}: {
  channels: ChannelsResponse | null;
  loading: boolean;
  errorText: string | null;
  selectedRunId: string | null;
}) {
  if (loading) {
    return <LoadingState label="正在加载通道波形…" />;
  }
  if (errorText) {
    return (
      <div className="m1ErrorBox" role="alert">
        <h3>通道加载失败</h3>
        <p>{errorText}</p>
      </div>
    );
  }
  if (!channels) {
    return <EmptyState title="尚无波形" detail="选择会话后将加载原始通道。" />;
  }

  const rawEntries = Object.entries(channels.raw);
  const processedEntries = Object.entries(channels.processed);

  return (
    <section className="m1Panel" data-testid="m1-waveform-panel">
      <div className="sectionHeading">
        <div>
          <h2>波形视图</h2>
          <p>
            原始通道为采集真相；处理后波形仅在已选 committed run 时由后端返回。
            {selectedRunId ? ` 当前 run=${selectedRunId}` : ' 当前未选 run，仅显示 raw。'}
          </p>
        </div>
      </div>
      <h3>Raw（主视图）</h3>
      {rawEntries.length === 0 ? (
        <EmptyState title="后端未返回可用原始通道" />
      ) : (
        rawEntries.map(([key, series]) => (
          <SeriesChart key={`raw-${key}`} title={series.name || key} series={series} />
        ))
      )}
      <h3>Processed（派生视图）</h3>
      {!selectedRunId ? (
        <p>未选择 committed run：不请求/不展示 processed。</p>
      ) : processedEntries.length === 0 ? (
        <p>该 run 未返回 processed 序列。</p>
      ) : (
        processedEntries.map(([key, series]) => (
          <SeriesChart
            key={`proc-${key}`}
            title={series.name || key}
            series={series}
            variant="processed"
          />
        ))
      )}
    </section>
  );
}
