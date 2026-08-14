import {describe, expect, it, vi} from 'vitest';
import {parseM1ApiError, listM1Sessions, replayM1Session} from '../m1/api';
import {describeM1ApiError} from '../m1/errorMessages';
import {formatNullableNumber} from '../m1/format';
import {buildPolylinePoints} from '../m1/chart';
import {shouldDropStaleResponse} from '../m1/M1Workspace';
import {isSentinelSoftwareCommitSha, readClientSoftwareCommitSha} from '../m1/softwareCommit';
import {apiErrorEnvelope} from './fixtures';

describe('M1 API error parsing', () => {
  it('parses stable envelope codes', async () => {
    const response = new Response(JSON.stringify(apiErrorEnvelope('artifact_conflict', 'Artifact conflict.')), {
      status: 409,
      headers: {'Content-Type': 'application/json'},
    });
    const error = await parseM1ApiError(response);
    expect(error.code).toBe('artifact_conflict');
    expect(describeM1ApiError(error)).toContain('run_id 已存在');
  });

  it('sanitizes unknown JSON failures', async () => {
    const response = new Response('not-json', {status: 500});
    const error = await parseM1ApiError(response);
    expect(error.code).toBe('unknown_error');
    expect(error.message).toContain('HTTP 500');
  });
});

describe('M1 formatting safety', () => {
  it('does not coerce null to zero', () => {
    expect(formatNullableNumber(null)).toBe('不可用');
    expect(formatNullableNumber(Number.NaN)).toBe('数值异常');
    expect(formatNullableNumber(1.2345, 2)).toBe('1.23');
  });

  it('handles empty or single-point charts', () => {
    expect(buildPolylinePoints([], 50).usable).toBe(false);
    expect(buildPolylinePoints([3], 50).usable).toBe(true);
    expect(buildPolylinePoints([1, Number.NaN, 2], 50).usable).toBe(true);
  });
});

describe('request race guards', () => {
  it('drops stale session/run responses', () => {
    expect(shouldDropStaleResponse(1, 1, 2, 1)).toBe(true);
    expect(shouldDropStaleResponse(2, 1, 2, 2)).toBe(true);
    expect(shouldDropStaleResponse(2, 2, 2, 2)).toBe(false);
  });
});

describe('listM1Sessions fetch', () => {
  it('returns typed payload on ok', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({api_version: 'm1-p3c-api-v1', sessions: []}), {
          status: 200,
          headers: {'Content-Type': 'application/json'},
        }),
      ),
    );
    const payload = await listM1Sessions();
    expect(payload.sessions).toEqual([]);
    vi.unstubAllGlobals();
  });
});

describe('replay provenance payload', () => {
  it('does not invent zero software_commit_sha when omitted', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          api_version: 'm1-p3c-api-v1',
          session_id: 'session-a',
          run_id: null,
          persisted: false,
          sp_result_sha256: 'f'.repeat(64),
          analysis: {formal_parameters: null},
        }),
        {status: 200, headers: {'Content-Type': 'application/json'}},
      ),
    );
    vi.stubGlobal('fetch', fetchMock);
    await replayM1Session('session-a', {persist: false, run_id: null});
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const body = JSON.parse(String(init.body ?? '{}')) as {software_commit_sha?: string};
    expect(body).not.toHaveProperty('software_commit_sha');
    vi.unstubAllGlobals();
  });

  it('drops sentinel zero software_commit_sha if supplied', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          api_version: 'm1-p3c-api-v1',
          session_id: 'session-a',
          run_id: null,
          persisted: false,
          sp_result_sha256: 'f'.repeat(64),
          analysis: {formal_parameters: null},
        }),
        {status: 200, headers: {'Content-Type': 'application/json'}},
      ),
    );
    vi.stubGlobal('fetch', fetchMock);
    await replayM1Session('session-a', {
      persist: false,
      run_id: null,
      software_commit_sha: '0'.repeat(40),
    });
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const body = JSON.parse(String(init.body ?? '{}')) as {software_commit_sha?: string};
    expect(body).not.toHaveProperty('software_commit_sha');
    vi.unstubAllGlobals();
  });

  it('recognizes sentinel and exposes injected client sha in test mode', () => {
    expect(isSentinelSoftwareCommitSha('0'.repeat(40))).toBe(true);
    expect(isSentinelSoftwareCommitSha('c'.repeat(40))).toBe(false);
    const injected = readClientSoftwareCommitSha();
    expect(injected).toMatch(/^[0-9a-f]{40}$/);
    expect(injected).not.toBe('0'.repeat(40));
    // 默认 Vitest 模式注入固定非哨兵值；允许测试覆盖 env 覆盖
    expect(injected).toBe('c'.repeat(40));
  });

  it('normalizes uppercase sha and rejects short/invalid payloads', async () => {
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          api_version: 'm1-p3c-api-v1',
          session_id: 'session-a',
          run_id: null,
          persisted: false,
          sp_result_sha256: 'f'.repeat(64),
          analysis: {formal_parameters: null},
        }),
        {status: 200, headers: {'Content-Type': 'application/json'}},
      ),
    );
    vi.stubGlobal('fetch', fetchMock);

    await replayM1Session('session-a', {
      persist: false,
      run_id: null,
      software_commit_sha: 'A'.repeat(40),
    });
    expect(fetchMock.mock.calls.length).toBeGreaterThan(0);
    let body = JSON.parse(
      String(((fetchMock.mock.calls[0] as unknown as [RequestInfo, RequestInit?])[1] ?? {}).body ?? '{}'),
    ) as {software_commit_sha?: string};
    expect(body.software_commit_sha).toBe('a'.repeat(40));

    fetchMock.mockClear();
    await replayM1Session('session-a', {
      persist: false,
      run_id: null,
      software_commit_sha: 'abc',
    });
    expect(fetchMock.mock.calls.length).toBeGreaterThan(0);
    body = JSON.parse(
      String(((fetchMock.mock.calls[0] as unknown as [RequestInfo, RequestInit?])[1] ?? {}).body ?? '{}'),
    ) as {software_commit_sha?: string};
    expect(body).not.toHaveProperty('software_commit_sha');

    fetchMock.mockClear();
    await replayM1Session('session-a', {
      persist: false,
      run_id: null,
      software_commit_sha: 'g'.repeat(40),
    });
    expect(fetchMock.mock.calls.length).toBeGreaterThan(0);
    body = JSON.parse(
      String(((fetchMock.mock.calls[0] as unknown as [RequestInfo, RequestInit?])[1] ?? {}).body ?? '{}'),
    ) as {software_commit_sha?: string};
    expect(body).not.toHaveProperty('software_commit_sha');
    vi.unstubAllGlobals();
  });
});
