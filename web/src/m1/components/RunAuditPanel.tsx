import React from 'react';
import type {RunDetail, RunSummary} from '../types';
import {EmptyState, HashValue, LoadingState} from './EmptyState';

export function RunAuditPanel({
  runs,
  currentRunId,
  selectedRunId,
  runDetail,
  loadingList,
  loadingDetail,
  listError,
  detailError,
  onSelectRun,
}: {
  runs: RunSummary[];
  currentRunId: string | null;
  selectedRunId: string | null;
  runDetail: RunDetail | null;
  loadingList: boolean;
  loadingDetail: boolean;
  listError: string | null;
  detailError: string | null;
  onSelectRun: (runId: string) => void;
}) {
  return (
    <section className="m1Panel" data-testid="m1-run-audit-panel">
      <div className="sectionHeading">
        <div>
          <h2>已提交 Run / 审计</h2>
          <p>仅展示后端 committed runs；current_run_id 由后端声明。</p>
        </div>
      </div>
      {loadingList ? <LoadingState label="正在加载 Run 列表…" /> : null}
      {listError ? (
        <div className="m1ErrorBox" role="alert">
          <h3>Run 列表失败</h3>
          <p>{listError}</p>
        </div>
      ) : null}
      {!loadingList && !listError && runs.length === 0 ? (
        <EmptyState title="尚无已提交 Run" />
      ) : (
        <div className="m1RunList">
          {runs.map((runItem) => {
            const selected = runItem.run_id === selectedRunId;
            const isCurrent = runItem.run_id === currentRunId;
            return (
              <button
                type="button"
                key={runItem.run_id}
                className={`m1RunCard${selected ? ' selected' : ''}`}
                aria-pressed={selected}
                onClick={() => onSelectRun(runItem.run_id)}
              >
                <strong>
                  {runItem.run_id}
                  {isCurrent ? ' · 当前 Run' : ''}
                </strong>
                <span>state：{runItem.state}</span>
                <span>committed_at_utc：{runItem.committed_at_utc}</span>
                <span>execution_mode：{runItem.execution_mode}</span>
                <span>asset_roles：{runItem.asset_roles.join(', ') || '无'}</span>
              </button>
            );
          })}
        </div>
      )}

      {loadingDetail ? <LoadingState label="正在加载 Run 详情…" /> : null}
      {detailError ? (
        <div className="m1ErrorBox" role="alert">
          <h3>Run 详情失败</h3>
          <p>{detailError}</p>
        </div>
      ) : null}
      {runDetail ? (
        <div data-testid="m1-run-detail">
          <h3>Run 详情</h3>
          <p>
            {runDetail.run.run_id} · {runDetail.run.state} · {runDetail.run.execution_mode}
          </p>
          <ul className="m1KeyList">
            {runDetail.assets.map((asset) => (
              <li key={`${asset.role}-${asset.relative_path}`}>
                工件引用 role={asset.role} · relative_path={asset.relative_path} · size=
                {asset.size_bytes}
                <HashValue value={asset.sha256} label="sha256" />
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

export function ProvenancePanel({
  analysis,
}: {
  analysis: import('../types').AppAnalysis | null;
}) {
  if (!analysis?.provenance) {
    return <EmptyState title="尚无 provenance" />;
  }
  const provenance = analysis.provenance;
  return (
    <section className="m1Panel" data-testid="m1-provenance-panel">
      <h2>溯源 / 版本</h2>
      <ul className="m1KeyList">
        <li>app_processing_version：{provenance.app_processing_version ?? '不可用'}</li>
        <li>app_manifest_schema_version：{provenance.app_manifest_schema_version ?? '不可用'}</li>
        <li>app_execution_mode：{provenance.app_execution_mode ?? '不可用'}</li>
        <li>
          <HashValue value={provenance.app_software_commit_sha} label="app_software_commit_sha" />
        </li>
        <li>sp_processing_version：{provenance.sp_processing_version ?? '不可用'}</li>
        <li>sp_parameter_version：{provenance.sp_parameter_version ?? '不可用'}</li>
        <li>
          <HashValue value={provenance.sp_parameter_digest} label="sp_parameter_digest" />
        </li>
        <li>
          sp_semantic_fingerprint_version：
          {provenance.sp_semantic_fingerprint_version ?? '不可用'}
        </li>
        <li>
          <HashValue value={provenance.sp_result_sha256} label="sp_result_sha256" />
        </li>
        <li>
          semantic_fingerprint_version：{analysis.semantic_fingerprint_version ?? '不可用'}
        </li>
        <li>
          <HashValue
            value={analysis.semantic_fingerprint_sha256}
            label="semantic_fingerprint_sha256"
          />
        </li>
      </ul>
    </section>
  );
}
