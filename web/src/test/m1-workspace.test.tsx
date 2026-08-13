import React from 'react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {cleanup, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {M1Workspace} from '../m1/M1Workspace';
import {
  apiErrorEnvelope,
  fixtureSessions,
  makeBlockedAnalysis,
  makeChannels,
  makeReplayResponse,
  makeRunDetail,
  makeRuns,
  makeSessionDetail,
} from './fixtures';

type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T) => void;
};

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((innerResolve) => {
    resolve = innerResolve;
  });
  return {promise, resolve};
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {'Content-Type': 'application/json'},
  });
}

function matchUrl(input: RequestInfo | URL, fragment: string): boolean {
  const text = typeof input === 'string' ? input : input.toString();
  return text.includes(fragment);
}

describe('M1Workspace UI', () => {
  beforeEach(() => {
    cleanup();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    cleanup();
  });

  it('loads session list, detail, raw waveform, blocked analysis, provenance and replay defaults', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (matchUrl(url, '/api/m1/sessions') && !url.includes('/sessions/')) {
        return jsonResponse(fixtureSessions);
      }
      if (url.endsWith('/api/m1/sessions/session-a')) {
        return jsonResponse(makeSessionDetail('session-a'));
      }
      if (url.includes('/sessions/session-a/runs') && !url.includes('/runs/')) {
        return jsonResponse(makeRuns('session-a'));
      }
      if (url.includes('/sessions/session-a/runs/run-a1')) {
        return jsonResponse(makeRunDetail('session-a', 'run-a1'));
      }
      if (url.includes('/sessions/session-a/channels')) {
        const hasRun = url.includes('run_id=run-a1');
        return jsonResponse(makeChannels('session-a', hasRun ? 'run-a1' : null));
      }
      if (url.includes('/sessions/session-a/analysis')) {
        return jsonResponse(makeBlockedAnalysis('session-a', 'run-a1'));
      }
      if (url.includes('/sessions/session-a/replay') && init?.method === 'POST') {
        const body = JSON.parse(String(init.body ?? '{}')) as {persist?: boolean; run_id?: string | null};
        expect(body.persist ?? false).toBe(false);
        return jsonResponse(makeReplayResponse('session-a', false));
      }
      return jsonResponse(apiErrorEnvelope('unknown_error', 'unexpected'), 500);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<M1Workspace />);

    expect(await screen.findByTestId('m1-session-list')).toBeInTheDocument();
    await user.click(screen.getByRole('button', {name: /session-a/}));

    expect(await screen.findByTestId('m1-session-overview')).toBeInTheDocument();
    expect(screen.getByTestId('m1-formal-safety')).toHaveTextContent('formal_parameters = null');
    expect(screen.getByTestId('m1-limitation-synthetic')).toBeInTheDocument();
    expect(screen.getByTestId('m1-limitation-h1')).toBeInTheDocument();

    expect(await screen.findByTestId('m1-waveform-panel')).toBeInTheDocument();
    expect(screen.getByText(/Raw（主视图）/)).toBeInTheDocument();
    expect(screen.getByText(/processed \/ filter view/)).toBeInTheDocument();

    expect(await screen.findByTestId('m1-quality-blocked')).toBeInTheDocument();
    expect(screen.getByTestId('m1-quality-panel')).toHaveTextContent('formal_parameters = null');
    expect(screen.getByTestId('m1-integrity-panel')).toBeInTheDocument();
    expect(screen.getByTestId('m1-beat-panel')).toBeInTheDocument();
    expect(screen.getByTestId('m1-reference-panel')).toHaveTextContent('不可用，非 0% 匹配');
    expect(screen.getByTestId('m1-provenance-panel')).toBeInTheDocument();
    expect(screen.getByTestId('m1-real-calibration-pending')).toHaveTextContent('等待 H1 校准');

    expect(screen.getByTestId('m1-replay-default-hint')).toHaveTextContent('persist=false');
    await user.click(screen.getByTestId('m1-replay-submit'));
    expect(await screen.findByTestId('m1-replay-result')).toHaveTextContent('临时重放');
  });

  it('shows analysis_not_available as unavailable, not HTTP crash', async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (matchUrl(url, '/api/m1/sessions') && !url.includes('/sessions/')) {
          return jsonResponse(fixtureSessions);
        }
        if (url.endsWith('/api/m1/sessions/session-a')) {
          return jsonResponse(makeSessionDetail('session-a'));
        }
        if (url.includes('/sessions/session-a/runs') && !url.includes('/runs/')) {
          return jsonResponse(makeRuns('session-a'));
        }
        if (url.includes('/sessions/session-a/runs/run-a1')) {
          return jsonResponse(makeRunDetail('session-a', 'run-a1'));
        }
        if (url.includes('/channels')) {
          return jsonResponse(makeChannels('session-a', url.includes('run_id=') ? 'run-a1' : null));
        }
        if (url.includes('/analysis')) {
          return jsonResponse(apiErrorEnvelope('analysis_not_available', 'Analysis is not available.'), 404);
        }
        return jsonResponse(apiErrorEnvelope('unknown_error', 'unexpected'), 500);
      }),
    );

    render(<M1Workspace />);
    await user.click(await screen.findByRole('button', {name: /session-a/}));
    expect(await screen.findByTestId('m1-analysis-unavailable')).toBeInTheDocument();
  });

  it('requires run_id for persisted replay and shows 409 conflict safely', async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (matchUrl(url, '/api/m1/sessions') && !url.includes('/sessions/')) {
          return jsonResponse(fixtureSessions);
        }
        if (url.endsWith('/api/m1/sessions/session-a')) {
          return jsonResponse(makeSessionDetail('session-a'));
        }
        if (url.includes('/sessions/session-a/runs') && !url.includes('/runs/')) {
          return jsonResponse(makeRuns('session-a'));
        }
        if (url.includes('/sessions/session-a/runs/run-a1')) {
          return jsonResponse(makeRunDetail('session-a', 'run-a1'));
        }
        if (url.includes('/channels')) {
          return jsonResponse(makeChannels('session-a', url.includes('run_id=') ? 'run-a1' : null));
        }
        if (url.includes('/analysis')) {
          return jsonResponse(makeBlockedAnalysis('session-a', 'run-a1'));
        }
        if (url.includes('/replay') && init?.method === 'POST') {
          return jsonResponse(apiErrorEnvelope('artifact_conflict', 'Artifact conflict.'), 409);
        }
        return jsonResponse(apiErrorEnvelope('unknown_error', 'unexpected'), 500);
      }),
    );

    render(<M1Workspace />);
    await user.click(await screen.findByRole('button', {name: /session-a/}));
    await screen.findByTestId('m1-replay-panel');

    const submit = screen.getByTestId('m1-replay-submit');
    await user.click(screen.getByTestId('m1-replay-persist'));
    expect(submit).toBeDisabled();
    await user.type(screen.getByTestId('m1-replay-run-id'), 'run-dup');
    expect(submit).not.toBeDisabled();
    await user.click(submit);
    expect(await screen.findByTestId('m1-replay-error')).toHaveTextContent('run_id 已存在');
  });

  it('protects session selection race', async () => {
    const user = userEvent.setup();
    const sessionADetail = deferred<Response>();
    const sessionBDetail = deferred<Response>();

    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (matchUrl(url, '/api/m1/sessions') && !url.includes('/sessions/')) {
          return jsonResponse(fixtureSessions);
        }
        if (url.endsWith('/api/m1/sessions/session-a')) {
          return sessionADetail.promise;
        }
        if (url.endsWith('/api/m1/sessions/session-b')) {
          return sessionBDetail.promise;
        }
        if (url.includes('/sessions/session-a/runs')) {
          return jsonResponse({
            api_version: 'm1-p3c-api-v1',
            session_id: 'session-a',
            current_run_id: null,
            runs: [],
          });
        }
        if (url.includes('/sessions/session-b/runs')) {
          return jsonResponse({
            api_version: 'm1-p3c-api-v1',
            session_id: 'session-b',
            current_run_id: null,
            runs: [],
          });
        }
        if (url.includes('/channels')) {
          const sessionId = url.includes('session-b') ? 'session-b' : 'session-a';
          return jsonResponse(makeChannels(sessionId, null));
        }
        return jsonResponse(apiErrorEnvelope('unknown_error', 'unexpected'), 500);
      }),
    );

    render(<M1Workspace />);
    const list = await screen.findByTestId('m1-session-list');
    await user.click(within(list).getByRole('button', {name: /session-a/}));
    await user.click(within(list).getByRole('button', {name: /session-b/}));

    // 迟到的 A 详情在 B 之后到达，不得覆盖 B
    sessionBDetail.resolve(jsonResponse({
      ...makeSessionDetail('session-b'),
      app_registered: false,
      committed_run_count: 0,
      current_run_id: null,
      runs: [],
    }));
    sessionADetail.resolve(jsonResponse(makeSessionDetail('session-a')));

    await waitFor(() => {
      expect(screen.getByTestId('m1-session-overview')).toHaveTextContent('session-b');
    });
    expect(screen.getByTestId('m1-session-overview')).not.toHaveTextContent('session-a');
  });

  it('protects run selection race for analysis', async () => {
    const user = userEvent.setup();
    const analysisA = deferred<Response>();
    const analysisB = deferred<Response>();

    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (matchUrl(url, '/api/m1/sessions') && !url.includes('/sessions/')) {
          return jsonResponse(fixtureSessions);
        }
        if (url.endsWith('/api/m1/sessions/session-a')) {
          return jsonResponse(makeSessionDetail('session-a'));
        }
        if (url.includes('/sessions/session-a/runs') && !url.includes('/runs/')) {
          return jsonResponse(makeRuns('session-a'));
        }
        if (url.includes('/runs/run-a1') && !url.includes('analysis') && !url.includes('channels')) {
          return jsonResponse(makeRunDetail('session-a', 'run-a1'));
        }
        if (url.includes('/runs/run-a2')) {
          return jsonResponse(makeRunDetail('session-a', 'run-a2'));
        }
        if (url.includes('/channels')) {
          const runId = url.includes('run_id=run-a2')
            ? 'run-a2'
            : url.includes('run_id=run-a1')
              ? 'run-a1'
              : null;
          return jsonResponse(makeChannels('session-a', runId));
        }
        if (url.includes('/analysis') && url.includes('run_id=run-a1')) {
          return analysisA.promise;
        }
        if (url.includes('/analysis') && url.includes('run_id=run-a2')) {
          return analysisB.promise;
        }
        if (url.includes('/analysis')) {
          return analysisA.promise;
        }
        return jsonResponse(apiErrorEnvelope('unknown_error', 'unexpected'), 500);
      }),
    );

    render(<M1Workspace />);
    await user.click(await screen.findByRole('button', {name: /session-a/}));
    await screen.findByTestId('m1-run-audit-panel');
    await user.click(screen.getByRole('button', {name: /run-a2/}));

    analysisB.resolve(
      jsonResponse({
        ...makeBlockedAnalysis('session-a', 'run-a2'),
        analysis: {
          ...makeBlockedAnalysis('session-a', 'run-a2').analysis,
          provenance: {
            ...makeBlockedAnalysis('session-a', 'run-a2').analysis.provenance!,
            sp_result_sha256: 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
          },
        },
      }),
    );
    analysisA.resolve(jsonResponse(makeBlockedAnalysis('session-a', 'run-a1')));

    await waitFor(() => {
      expect(screen.getByTestId('m1-provenance-panel')).toHaveTextContent('bbbbbbbbbbbb');
    });
  });
});
