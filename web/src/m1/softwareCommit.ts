/** 客户端软件提交标识：禁止伪造全零 SHA 作为审计 provenance。 */

const ZERO_SHA = '0'.repeat(40);
const HEX_SHA40 = /^[0-9a-f]{40}$/;

/** 判定是否为契约默认哨兵（全零），不得写入持久化审计。 */
export function isSentinelSoftwareCommitSha(value: string | null | undefined): boolean {
  return typeof value === 'string' && value === ZERO_SHA;
}

/** 读取构建期注入的真实软件提交；无效或哨兵时返回 null。 */
export function readClientSoftwareCommitSha(): string | null {
  const raw = import.meta.env.VITE_M1_SOFTWARE_COMMIT_SHA;
  if (typeof raw !== 'string') {
    return null;
  }
  const normalized = raw.trim().toLowerCase();
  if (!HEX_SHA40.test(normalized) || isSentinelSoftwareCommitSha(normalized)) {
    return null;
  }
  return normalized;
}
