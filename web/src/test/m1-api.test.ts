import {describe, expect, it, vi} from 'vitest';
import {parseM1ApiError, listM1Sessions} from '../m1/api';
import {describeM1ApiError} from '../m1/errorMessages';
import {formatNullableNumber} from '../m1/format';
import {buildPolylinePoints} from '../m1/chart';
import {shouldDropStaleResponse} from '../m1/M1Workspace';
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
