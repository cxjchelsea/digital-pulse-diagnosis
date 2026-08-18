"""P4A 类型化规则引擎错误。错误码不是 M1Decision reason_codes。"""

from __future__ import annotations


class M1IntError(ValueError):
    """INT 规则核心失败关闭错误。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
