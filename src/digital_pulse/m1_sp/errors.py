"""SP-S1-pre typed errors."""

from __future__ import annotations


class SPError(ValueError):
    """Structured preprocessing error with a stable machine code."""

    def __init__(self, code: str, message: str | None = None):
        super().__init__(message or code)
        self.code = code
