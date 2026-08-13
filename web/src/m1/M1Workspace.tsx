import React, {useCallback, useEffect, useRef, useState} from 'react';
import {
  getM1Analysis,
  getM1Channels,
  getM1Run,
  getM1Session,
  isAbortError,
  listM1Runs,
  listM1Sessions,
  replayM1Session,
} from './api';
import {describeM1ApiError} from './errorMessages';
import {BeatReferencePanel} from './components/BeatReferencePanel';
import {QualityPanel, IntegrityPanel} from './components/QualityPanel';
import {ReplayPanel} from './components/ReplayPanel';
import {ProvenancePanel, RunAuditPanel} from './components/RunAuditPanel';
import {SessionList} from './components/SessionList';
import {SessionOverview} from './components/SessionOverview';
import {WaveformPanel} from './components/WaveformPanel';
import type {
  AnalysisResponse,
  AppAnalysis,
  ChannelsResponse,
  M1ApiError,
  ReplayResponse,
  RunDetail,
  RunSummary,
  SessionDetail,
  SessionSummary,
} from './types';

function bump(ref: React.MutableRefObject<number>): number {
  ref.current += 1;
  return ref.current;
}

export function M1Workspace() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  const [sessionsError, setSessionsError] = useState<string | null>(null);

  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [sessionDetail, setSessionDetail] = useState<SessionDetail | null>(null);
  const [sessionLoading, setSessionLoading] = useState(false);
  const [sessionError, setSessionError] = useState<string | null>(null);

  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [currentRunId, setCurrentRunId] = useState<string | null>(null);
  const [runsLoading, setRunsLoading] = useState(false);
  const [runsError, setRunsError] = useState<string | null>(null);

  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [runDetail, setRunDetail] = useState<RunDetail | null>(null);
  const [runDetailLoading, setRunDetailLoading] = useState(false);
  const [runDetailError, setRunDetailError] = useState<string | null>(null);

  const [channels, setChannels] = useState<ChannelsResponse | null>(null);
  const [channelsLoading, setChannelsLoading] = useState(false);
  const [channelsError, setChannelsError] = useState<string | null>(null);

  const [analysis, setAnalysis] = useState<AppAnalysis | null>(null);
  const [analysisRunId, setAnalysisRunId] = useState<string | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [analysisUnavailable, setAnalysisUnavailable] = useState(false);

  const [replayBusy, setReplayBusy] = useState(false);
  const [replayResult, setReplayResult] = useState<ReplayResponse | null>(null);
  const [replayError, setReplayError] = useState<M1ApiError | null>(null);

  const sessionTokenRef = useRef(0);
  const runTokenRef = useRef(0);
  const sessionAbortRef = useRef<AbortController | null>(null);
  const runAbortRef = useRef<AbortController | null>(null);
  const channelsAbortRef = useRef<AbortController | null>(null);
  const listAbortRef = useRef<AbortController | null>(null);
  const selectedSessionIdRef = useRef<string | null>(null);
  const selectedRunIdRef = useRef<string | null>(null);

  const refreshSessions = useCallback(async () => {
    listAbortRef.current?.abort();
    const controller = new AbortController();
    listAbortRef.current = controller;
    setSessionsLoading(true);
    setSessionsError(null);
    try {
      const payload = await listM1Sessions(controller.signal);
      setSessions(payload.sessions);
    } catch (error) {
      if (isAbortError(error)) {
        return;
      }
      setSessionsError(describeM1ApiError(error as M1ApiError));
      setSessions([]);
    } finally {
      if (!controller.signal.aborted) {
        setSessionsLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void refreshSessions();
    return () => {
      listAbortRef.current?.abort();
      sessionAbortRef.current?.abort();
      runAbortRef.current?.abort();
      channelsAbortRef.current?.abort();
    };
  }, [refreshSessions]);

  const loadChannels = useCallback(
    async (
      sessionId: string,
      runId: string | null,
      sessionToken: number,
      runToken: number,
    ) => {
      channelsAbortRef.current?.abort();
      const controller = new AbortController();
      channelsAbortRef.current = controller;
      setChannelsLoading(true);
      setChannelsError(null);
      try {
        const payload = await getM1Channels(sessionId, {
          runId,
          maxPoints: 800,
          signal: controller.signal,
        });
        // 会话或 run 已切换则丢弃
        if (sessionToken !== sessionTokenRef.current || runToken !== runTokenRef.current) {
          return;
        }
        if (selectedSessionIdRef.current !== sessionId) {
          return;
        }
        if ((selectedRunIdRef.current ?? null) !== (runId ?? null)) {
          return;
        }
        setChannels(payload);
      } catch (error) {
        if (isAbortError(error) || controller.signal.aborted) {
          return;
        }
        if (sessionToken !== sessionTokenRef.current || runToken !== runTokenRef.current) {
          return;
        }
        setChannels(null);
        setChannelsError(describeM1ApiError(error as M1ApiError));
      } finally {
        if (
          !controller.signal.aborted &&
          sessionToken === sessionTokenRef.current &&
          runToken === runTokenRef.current
        ) {
          setChannelsLoading(false);
        }
      }
    },
    [],
  );

  const loadAnalysisForRun = useCallback(
    async (
      sessionId: string,
      runId: string,
      sessionToken: number,
      runToken: number,
      signal: AbortSignal,
    ) => {
      setAnalysisLoading(true);
      setAnalysisError(null);
      setAnalysisUnavailable(false);
      try {
        const payload: AnalysisResponse = await getM1Analysis(sessionId, {
          runId,
          signal,
        });
        if (sessionToken !== sessionTokenRef.current || runToken !== runTokenRef.current) {
          return;
        }
        setAnalysis(payload.analysis);
        setAnalysisRunId(payload.run_id);
      } catch (error) {
        if (isAbortError(error) || signal.aborted) {
          return;
        }
        if (sessionToken !== sessionTokenRef.current || runToken !== runTokenRef.current) {
          return;
        }
        const apiError = error as M1ApiError;
        setAnalysis(null);
        setAnalysisRunId(null);
        if (apiError.code === 'analysis_not_available') {
          setAnalysisUnavailable(true);
          setAnalysisError(null);
        } else {
          setAnalysisUnavailable(false);
          setAnalysisError(describeM1ApiError(apiError));
        }
      } finally {
        if (
          !signal.aborted &&
          sessionToken === sessionTokenRef.current &&
          runToken === runTokenRef.current
        ) {
          setAnalysisLoading(false);
        }
      }
    },
    [],
  );

  const selectRun = useCallback(
    async (sessionId: string, runId: string, sessionToken: number) => {
      runAbortRef.current?.abort();
      const controller = new AbortController();
      runAbortRef.current = controller;
      const runToken = bump(runTokenRef);

      selectedRunIdRef.current = runId;
      setSelectedRunId(runId);
      setRunDetail(null);
      setRunDetailError(null);
      setRunDetailLoading(true);
      setReplayResult(null);
      setReplayError(null);
      setAnalysis(null);
      setAnalysisRunId(null);
      setAnalysisUnavailable(false);
      setAnalysisError(null);

      void loadChannels(sessionId, runId, sessionToken, runToken);
      void loadAnalysisForRun(sessionId, runId, sessionToken, runToken, controller.signal);

      try {
        const detail = await getM1Run(sessionId, runId, controller.signal);
        if (sessionToken !== sessionTokenRef.current || runToken !== runTokenRef.current) {
          return;
        }
        setRunDetail(detail);
      } catch (error) {
        if (isAbortError(error) || controller.signal.aborted) {
          return;
        }
        if (sessionToken !== sessionTokenRef.current || runToken !== runTokenRef.current) {
          return;
        }
        setRunDetailError(describeM1ApiError(error as M1ApiError));
      } finally {
        if (
          !controller.signal.aborted &&
          sessionToken === sessionTokenRef.current &&
          runToken === runTokenRef.current
        ) {
          setRunDetailLoading(false);
        }
      }
    },
    [loadAnalysisForRun, loadChannels],
  );

  const selectSession = useCallback(
    async (sessionId: string) => {
      sessionAbortRef.current?.abort();
      runAbortRef.current?.abort();
      channelsAbortRef.current?.abort();
      const controller = new AbortController();
      sessionAbortRef.current = controller;
      const sessionToken = bump(sessionTokenRef);
      const runToken = bump(runTokenRef); // 使旧 run/通道请求失效

      selectedSessionIdRef.current = sessionId;
      selectedRunIdRef.current = null;
      setSelectedSessionId(sessionId);
      setSessionDetail(null);
      setSessionError(null);
      setSessionLoading(true);
      setRuns([]);
      setRunsError(null);
      setRunsLoading(true);
      setCurrentRunId(null);
      setSelectedRunId(null);
      setRunDetail(null);
      setRunDetailError(null);
      setAnalysis(null);
      setAnalysisRunId(null);
      setAnalysisError(null);
      setAnalysisUnavailable(false);
      setChannels(null);
      setChannelsError(null);
      setReplayResult(null);
      setReplayError(null);

      // 无 run 时仅加载 raw
      void loadChannels(sessionId, null, sessionToken, runToken);

      try {
        const [detail, runsPayload] = await Promise.all([
          getM1Session(sessionId, controller.signal),
          listM1Runs(sessionId, controller.signal),
        ]);
        if (sessionToken !== sessionTokenRef.current) {
          return;
        }
        setSessionDetail(detail);
        setRuns(runsPayload.runs);
        setCurrentRunId(runsPayload.current_run_id);

        const preferredRun = runsPayload.current_run_id ?? runsPayload.runs[0]?.run_id ?? null;
        if (preferredRun) {
          void selectRun(sessionId, preferredRun, sessionToken);
        }
      } catch (error) {
        if (isAbortError(error) || controller.signal.aborted) {
          return;
        }
        if (sessionToken !== sessionTokenRef.current) {
          return;
        }
        setSessionError(describeM1ApiError(error as M1ApiError));
      } finally {
        if (!controller.signal.aborted && sessionToken === sessionTokenRef.current) {
          setSessionLoading(false);
          setRunsLoading(false);
        }
      }
    },
    [loadChannels, selectRun],
  );

  async function handleReplay(persist: boolean, runId: string | null) {
    if (!selectedSessionId) {
      return;
    }
    const sessionId = selectedSessionId;
    setReplayBusy(true);
    setReplayError(null);
    try {
      const result = await replayM1Session(sessionId, {
        persist,
        run_id: runId,
      });
      if (selectedSessionIdRef.current !== sessionId) {
        return;
      }
      setReplayResult(result);
      if (persist) {
        const [detail, runsPayload] = await Promise.all([
          getM1Session(sessionId),
          listM1Runs(sessionId),
        ]);
        if (selectedSessionIdRef.current !== sessionId) {
          return;
        }
        setSessionDetail(detail);
        setRuns(runsPayload.runs);
        setCurrentRunId(runsPayload.current_run_id);
      }
    } catch (error) {
      if (isAbortError(error)) {
        return;
      }
      if (selectedSessionIdRef.current !== sessionId) {
        return;
      }
      setReplayResult(null);
      setReplayError(error as M1ApiError);
    } finally {
      if (selectedSessionIdRef.current === sessionId) {
        setReplayBusy(false);
      }
    }
  }

  return (
    <div className="m1Workspace" data-testid="m1-workspace">
      <section className="controls m1Hero">
        <div>
          <h2>M1 工程分析工作区</h2>
          <p>
            可视化 / 导航 / 审计表面。UI 不做信号处理、质量引擎或正式参数计算。正式参数不可用 ·
            仅仿真/软件预验证 · 等待 H1 真实校准。
          </p>
        </div>
        <button type="button" className="secondary" onClick={() => void refreshSessions()}>
          刷新会话列表
        </button>
      </section>

      <div className="m1Layout">
        <aside className="m1Sidebar">
          <h2>会话浏览器</h2>
          <SessionList
            sessions={sessions}
            selectedSessionId={selectedSessionId}
            onSelect={(sessionId) => void selectSession(sessionId)}
            loading={sessionsLoading}
            errorText={sessionsError}
          />
        </aside>
        <div className="m1MainColumn">
          <SessionOverview
            detail={sessionDetail}
            loading={sessionLoading}
            errorText={sessionError}
          />
          <WaveformPanel
            channels={channels}
            loading={channelsLoading}
            errorText={channelsError}
            selectedRunId={selectedRunId}
          />
          <QualityPanel
            analysis={analysis}
            loading={analysisLoading}
            errorText={analysisError}
            unavailable={analysisUnavailable}
          />
          {analysis ? <IntegrityPanel analysis={analysis} /> : null}
          {analysis ? <BeatReferencePanel analysis={analysis} /> : null}
          <RunAuditPanel
            runs={runs}
            currentRunId={currentRunId}
            selectedRunId={selectedRunId}
            runDetail={runDetail}
            loadingList={runsLoading}
            loadingDetail={runDetailLoading}
            listError={runsError}
            detailError={runDetailError}
            onSelectRun={(runId) => {
              if (selectedSessionId) {
                void selectRun(selectedSessionId, runId, sessionTokenRef.current);
              }
            }}
          />
          {analysis ? <ProvenancePanel analysis={analysis} /> : null}
          {analysisRunId ? (
            <p className="m1Note">当前分析绑定 committed run：{analysisRunId}</p>
          ) : null}
          <ReplayPanel
            disabled={!selectedSessionId}
            busy={replayBusy}
            onReplay={(persist, runId) => void handleReplay(persist, runId)}
            lastResult={replayResult}
            lastError={replayError}
          />
        </div>
      </div>
    </div>
  );
}

/** 供测试的纯函数：判断迟到响应是否应丢弃。 */
export function shouldDropStaleResponse(
  requestSessionToken: number,
  requestRunToken: number,
  liveSessionToken: number,
  liveRunToken: number,
): boolean {
  return requestSessionToken !== liveSessionToken || requestRunToken !== liveRunToken;
}
