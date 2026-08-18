"""冻结 I1PolicyConfig 与配置摘要。不含 rule_version，不含 SP 阈值。"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import M1IntError
from .models import sha256_canonical

POLICY_SCHEMA_VERSION = "i1-policy-v1"
FROZEN_MAX_RETRY_COUNT = 2
PRIORITY_TABLE_VERSION = "i1-priority-v1"
RETRY_EXHAUSTION_POLICY_VERSION = "i1-retry-exhaustion-v1"


@dataclass(frozen=True, slots=True)
class I1PolicyConfig:
    policy_schema_version: str = POLICY_SCHEMA_VERSION
    max_retry_count: int = FROZEN_MAX_RETRY_COUNT
    priority_table_version: str = PRIORITY_TABLE_VERSION
    retry_exhaustion_policy_version: str = RETRY_EXHAUSTION_POLICY_VERSION


def policy_configuration_digest(policy: I1PolicyConfig) -> str:
    """对规范 I1PolicyConfig JSON 做 SHA-256，得到 64 位小写十六进制。"""

    payload = {
        "max_retry_count": policy.max_retry_count,
        "policy_schema_version": policy.policy_schema_version,
        "priority_table_version": policy.priority_table_version,
        "retry_exhaustion_policy_version": policy.retry_exhaustion_policy_version,
    }
    return sha256_canonical(payload)


def require_frozen_i1_policy(policy: I1PolicyConfig) -> None:
    """求值路径只接受冻结 I1-pre 策略；摘要测试可构造其它对象。"""

    if policy.policy_schema_version != POLICY_SCHEMA_VERSION:
        raise M1IntError("version_mismatch", "I1PolicyConfig.policy_schema_version must be i1-policy-v1")
    if policy.max_retry_count != FROZEN_MAX_RETRY_COUNT:
        raise M1IntError("version_mismatch", "I1PolicyConfig.max_retry_count must be 2 for i1-pre")
    if policy.priority_table_version != PRIORITY_TABLE_VERSION:
        raise M1IntError("version_mismatch", "unexpected priority_table_version")
    if policy.retry_exhaustion_policy_version != RETRY_EXHAUSTION_POLICY_VERSION:
        raise M1IntError("version_mismatch", "unexpected retry_exhaustion_policy_version")
