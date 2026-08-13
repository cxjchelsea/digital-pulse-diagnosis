/** 展示层格式化：不把 null 伪装成 0，不把非法数值伪装成可用。 */

export function formatNullableNumber(
  value: number | null | undefined,
  digits = 3,
): string {
  if (value === null || value === undefined) {
    return '不可用';
  }
  if (!Number.isFinite(value)) {
    return '数值异常';
  }
  return value.toFixed(digits);
}

export function formatNullableInteger(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return '不可用';
  }
  if (!Number.isFinite(value)) {
    return '数值异常';
  }
  return String(Math.trunc(value));
}

export function formatBoolean(value: boolean | null | undefined): string {
  if (value === null || value === undefined) {
    return '不可用';
  }
  return value ? '是' : '否';
}

export function abbreviateHash(value: string | null | undefined, keep = 12): string {
  if (!value) {
    return '不可用';
  }
  if (value.length <= keep) {
    return value;
  }
  return `${value.slice(0, keep)}…`;
}

export function joinCodes(codes: string[] | null | undefined): string {
  if (!codes || codes.length === 0) {
    return '无';
  }
  return codes.join(', ');
}

export function hasLimitation(
  limitations: string[] | null | undefined,
  code: string,
): boolean {
  return Array.isArray(limitations) && limitations.includes(code);
}
