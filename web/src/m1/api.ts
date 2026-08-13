import type {
  AnalysisResponse,
  ChannelsResponse,
  M1ApiError,
  ReplayRequest,
  ReplayResponse,
  RunDetail,
  RunsResponse,
  SessionDetail,
  SessionsResponse,
} from './types';

const API_BASE = '/api/m1';

function sanitizeMessage(raw: unknown): string {
  if (typeof raw !== 'string' || !raw.trim()) {
    return 'Request failed.';
  }
  // 不向 UI 透出本机路径痕迹
  return raw.replace(/[A-Za-z]:\\[^\s]+/g, '[path]').replace(/\/(?:home|Users|tmp|var)\/[^\s]+/g, '[path]');
}

export async function parseM1ApiError(response: Response): Promise<M1ApiError> {
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  const detail =
    payload && typeof payload === 'object' && 'detail' in payload
      ? (payload as {detail?: unknown}).detail
      : null;
  const envelope =
    detail && typeof detail === 'object' && 'error' in detail
      ? (detail as {error?: {code?: unknown; message?: unknown}}).error
      : null;

  const code =
    envelope && typeof envelope.code === 'string' && envelope.code
      ? envelope.code
      : 'unknown_error';
  const message =
    envelope && typeof envelope.message === 'string'
      ? sanitizeMessage(envelope.message)
      : `HTTP ${response.status}`;

  return {code, message, httpStatus: response.status};
}

async function requestJson<T>(
  path: string,
  init: RequestInit | undefined,
  signal?: AbortSignal,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      signal,
      headers: {
        Accept: 'application/json',
        ...(init?.headers ?? {}),
      },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw error;
    }
    const wrapped: M1ApiError = {
      code: 'unknown_error',
      message: 'Network request failed.',
      httpStatus: 0,
    };
    throw wrapped;
  }

  if (!response.ok) {
    throw await parseM1ApiError(response);
  }

  try {
    return (await response.json()) as T;
  } catch {
    const wrapped: M1ApiError = {
      code: 'unknown_error',
      message: 'Response JSON is invalid.',
      httpStatus: response.status,
    };
    throw wrapped;
  }
}

export function listM1Sessions(signal?: AbortSignal): Promise<SessionsResponse> {
  return requestJson<SessionsResponse>('/sessions', undefined, signal);
}

export function getM1Session(
  sessionId: string,
  signal?: AbortSignal,
): Promise<SessionDetail> {
  return requestJson<SessionDetail>(
    `/sessions/${encodeURIComponent(sessionId)}`,
    undefined,
    signal,
  );
}

export function getM1Channels(
  sessionId: string,
  options?: {runId?: string | null; maxPoints?: number; signal?: AbortSignal},
): Promise<ChannelsResponse> {
  const params = new URLSearchParams();
  if (options?.runId) {
    params.set('run_id', options.runId);
  }
  if (options?.maxPoints !== undefined) {
    params.set('max_points', String(options.maxPoints));
  }
  const query = params.toString();
  const suffix = query ? `?${query}` : '';
  return requestJson<ChannelsResponse>(
    `/sessions/${encodeURIComponent(sessionId)}/channels${suffix}`,
    undefined,
    options?.signal,
  );
}

export function getM1Analysis(
  sessionId: string,
  options?: {runId?: string | null; signal?: AbortSignal},
): Promise<AnalysisResponse> {
  const params = new URLSearchParams();
  if (options?.runId) {
    params.set('run_id', options.runId);
  }
  const query = params.toString();
  const suffix = query ? `?${query}` : '';
  return requestJson<AnalysisResponse>(
    `/sessions/${encodeURIComponent(sessionId)}/analysis${suffix}`,
    undefined,
    options?.signal,
  );
}

export function listM1Runs(
  sessionId: string,
  signal?: AbortSignal,
): Promise<RunsResponse> {
  return requestJson<RunsResponse>(
    `/sessions/${encodeURIComponent(sessionId)}/runs`,
    undefined,
    signal,
  );
}

export function getM1Run(
  sessionId: string,
  runId: string,
  signal?: AbortSignal,
): Promise<RunDetail> {
  return requestJson<RunDetail>(
    `/sessions/${encodeURIComponent(sessionId)}/runs/${encodeURIComponent(runId)}`,
    undefined,
    signal,
  );
}

export function replayM1Session(
  sessionId: string,
  body: ReplayRequest,
  signal?: AbortSignal,
): Promise<ReplayResponse> {
  return requestJson<ReplayResponse>(
    `/sessions/${encodeURIComponent(sessionId)}/replay`,
    {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        persist: body.persist ?? false,
        run_id: body.run_id ?? null,
        software_commit_sha: body.software_commit_sha ?? '0'.repeat(40),
      }),
    },
    signal,
  );
}

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError';
}
