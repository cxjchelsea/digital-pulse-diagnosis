import React from 'react';
import {formatBoolean, formatNullableNumber, hasLimitation} from '../format';
import type {SessionDetail} from '../types';
import {EmptyState, LoadingState} from './EmptyState';

export function SessionOverview({
  detail,
  loading,
  errorText,
}: {
  detail: SessionDetail | null;
  loading: boolean;
  errorText: string | null;
}) {
  if (loading) {
    return <LoadingState label="正在加载会话详情…" />;
  }
  if (errorText) {
    return (
      <div className="m1ErrorBox" role="alert">
        <h3>会话详情加载失败</h3>
        <p>{errorText}</p>
      </div>
    );
  }
  if (!detail) {
    return <EmptyState title="未选择会话" detail="从左侧选择一个会话以查看工程事实。" />;
  }

  const syntheticOnly = hasLimitation(detail.limitations, 'synthetic_only');
  const pendingH1 = hasLimitation(detail.limitations, 'pending_h1_calibration');

  return (
    <section className="m1Panel" data-testid="m1-session-overview">
      <div className="sectionHeading">
        <div>
          <h2>会话概览</h2>
          <p>只读展示 P3C 会话事实，前端不重算参数。</p>
        </div>
      </div>
      <div className="m1SafetyBanner" data-testid="m1-formal-safety">
        <p>
          <strong>正式参数不可用</strong>：formal_parameters ={' '}
          {detail.formal_parameters === null ? 'null' : String(detail.formal_parameters)}；
          formal_parameters_allowed = {formatBoolean(detail.formal_parameters_allowed)}
        </p>
        {syntheticOnly ? (
          <p data-testid="m1-limitation-synthetic">仅仿真/软件预验证（synthetic_only）</p>
        ) : null}
        {pendingH1 ? (
          <p data-testid="m1-limitation-h1">等待 H1 真实校准（pending_h1_calibration）</p>
        ) : null}
      </div>
      <div className="metrics m1Metrics">
        <article>
          <small>session_id</small>
          <strong>{detail.session_id}</strong>
        </article>
        <article>
          <small>来源类型</small>
          <strong>{detail.source_type}</strong>
        </article>
        <article>
          <small>完成状态</small>
          <strong>{detail.completed ? '已完成' : '未完成'}</strong>
        </article>
        <article>
          <small>完成原因</small>
          <strong>{detail.completion_reason ?? '—'}</strong>
        </article>
        <article>
          <small>采样率 Hz</small>
          <strong>{formatNullableNumber(detail.sample_rate_hz, 1)}</strong>
        </article>
        <article>
          <small>配置通道</small>
          <strong>{detail.configured_channels.join(', ') || '—'}</strong>
        </article>
        <article>
          <small>开始 UTC</small>
          <strong>{detail.started_at_utc}</strong>
        </article>
        <article>
          <small>结束 UTC</small>
          <strong>{detail.ended_at_utc ?? '—'}</strong>
        </article>
        <article>
          <small>原始持久化</small>
          <strong>{detail.raw_persistence_status}</strong>
        </article>
        <article>
          <small>参数状态</small>
          <strong>{detail.parameter_status}</strong>
        </article>
        <article>
          <small>原始完整性保证</small>
          <strong>{detail.raw_integrity_assurance ?? '—'}</strong>
        </article>
        <article>
          <small>已提交 Run 数</small>
          <strong>{detail.committed_run_count}</strong>
        </article>
        <article>
          <small>当前 Run</small>
          <strong>{detail.current_run_id ?? '无'}</strong>
        </article>
      </div>
      <p>
        限制项：{detail.limitations.length ? detail.limitations.join(', ') : '无'}
      </p>
      <p className="m1Note">报告能力将在 P3E 提供；本阶段仅做工程分析视图。</p>
    </section>
  );
}
