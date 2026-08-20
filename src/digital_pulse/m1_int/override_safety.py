"""P4B-A 操作者覆盖安全矩阵。纯函数，不写盘、不编排。"""

from __future__ import annotations

from enum import Enum

from digital_pulse.m1_contracts import I1_ACTIONS

from .errors import M1IntError

# I1-pre 允许的覆盖目标：只能保持或升级安全，不能削弱权威安全动作。
ALLOWED_OVERRIDE_TARGETS: dict[str, frozenset[str]] = {
    "accept": frozenset({"manual_review", "stop"}),
    "retry_same_position": frozenset({"stop", "manual_review"}),
    "reposition": frozenset({"stop", "manual_review"}),
    "manual_review": frozenset({"stop"}),
    "stop": frozenset({"abort_and_release"}),
    "abort_and_release": frozenset(),
}


class OverrideClassification(str, Enum):
    """覆盖请求分类（动作对），不是 ledger 幂等结论。

    IDEMPOTENT_SAME_ACTION 仅表示 machine_action == requested_action，
    不能替代“同一 event_id / 同一 canonical payload 的 ledger no-op”。
    真正的 append 幂等必须由完整事件身份判定。
    """

    ALLOWED = "allowed"
    REJECTED_BY_SAFETY = "rejected_by_safety"
    IDEMPOTENT_SAME_ACTION = "idempotent_same_action"


def _require_i1_action(field_name: str, action: str) -> str:
    if not isinstance(action, str) or action not in I1_ACTIONS:
        raise M1IntError("invalid_input", f"{field_name} must be a frozen I1 action")
    return action


def classify_override(machine_action: str, requested_action: str) -> OverrideClassification:
    """按冻结允许表分类覆盖请求；未知动作失败关闭。"""

    machine = _require_i1_action("machine_action", machine_action)
    requested = _require_i1_action("requested_action", requested_action)
    if machine == requested:
        return OverrideClassification.IDEMPOTENT_SAME_ACTION
    if requested in ALLOWED_OVERRIDE_TARGETS[machine]:
        return OverrideClassification.ALLOWED
    return OverrideClassification.REJECTED_BY_SAFETY


def is_override_allowed(machine_action: str, requested_action: str) -> bool:
    """仅当请求是明确允许的不同动作时为 True。"""

    return classify_override(machine_action, requested_action) is OverrideClassification.ALLOWED
