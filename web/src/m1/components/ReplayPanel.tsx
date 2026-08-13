import React, {useState} from 'react';
import type {AppAnalysis, M1ApiError, ReplayResponse} from '../types';
import {describeM1ApiError} from '../errorMessages';
import {HashValue} from './EmptyState';

export function ReplayPanel({
  disabled,
  busy,
  onReplay,
  lastResult,
  lastError,
}: {
  disabled: boolean;
  busy: boolean;
  onReplay: (persist: boolean, runId: string | null) => void;
  lastResult: ReplayResponse | null;
  lastError: M1ApiError | null;
}) {
  const [persistEnabled, setPersistEnabled] = useState(false);
  const [runIdInput, setRunIdInput] = useState('');

  function submit() {
    if (persistEnabled && !runIdInput.trim()) {
      return;
    }
    onReplay(persistEnabled, persistEnabled ? runIdInput.trim() : null);
  }

  return (
    <section className="m1Panel" data-testid="m1-replay-panel">
      <div className="sectionHeading">
        <div>
          <h2>显式软件重放</h2>
          <p>不会在打开会话/切换分析时自动重放。默认只读重放，不创建新 run。</p>
        </div>
      </div>
      <label className="m1Check">
        <input
          type="checkbox"
          checked={persistEnabled}
          onChange={(event) => setPersistEnabled(event.target.checked)}
          data-testid="m1-replay-persist"
        />
        将重放结果保存为新审计 run
      </label>
      {persistEnabled ? (
        <label className="m1Field">
          审计 run_id（必填）
          <input
            value={runIdInput}
            onChange={(event) => setRunIdInput(event.target.value)}
            placeholder="例如 run-audit-001"
            data-testid="m1-replay-run-id"
          />
        </label>
      ) : (
        <p data-testid="m1-replay-default-hint">默认 persist=false：只读重放，不创建新 run</p>
      )}
      <button
        type="button"
        onClick={submit}
        disabled={
          disabled || busy || (persistEnabled && !runIdInput.trim())
        }
        data-testid="m1-replay-submit"
      >
        {busy ? '重放中…' : '重新运行软件分析'}
      </button>
      {lastError ? (
        <div className="m1ErrorBox" role="alert" data-testid="m1-replay-error">
          <h3>重放错误</h3>
          <p>{describeM1ApiError(lastError)}</p>
        </div>
      ) : null}
      {lastResult ? (
        <div
          className={`m1ReplayResult${lastResult.persisted ? '' : ' ephemeral'}`}
          data-testid="m1-replay-result"
        >
          <h3>
            {lastResult.persisted ? '已提交 Run（持久化重放）' : '临时重放 / 未持久化结果'}
          </h3>
          <p>persisted={String(lastResult.persisted)} · run_id={lastResult.run_id ?? 'null'}</p>
          <HashValue value={lastResult.sp_result_sha256} label="sp_result_sha256" />
          <EphemeralGateNote analysis={lastResult.analysis} persisted={lastResult.persisted} />
        </div>
      ) : null}
    </section>
  );
}

function EphemeralGateNote({
  analysis,
  persisted,
}: {
  analysis: AppAnalysis;
  persisted: boolean;
}) {
  if (persisted) {
    return <p>该结果已写入审计 run，请在 Run 列表中确认后端 current_run_id。</p>;
  }
  return (
    <p>
      临时重放不得视为已提交 Run。formal_parameters=
      {analysis.formal_parameters === null ? 'null' : String(analysis.formal_parameters)}
    </p>
  );
}
