"""Structured, path-safe errors for the M1 APP persistence foundation."""

from __future__ import annotations

from typing import Any, Mapping


class M1AppError(ValueError):
    """Domain error safe to map to a future API response.

    Messages and details must contain logical asset identities only. Callers may
    log the original exception separately, but absolute filesystem paths are not
    part of this public/domain error surface.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        asset: str | None = None,
        details: Mapping[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.asset = asset
        self.details = dict(details or {})

    def to_public_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.asset is not None:
            payload["asset"] = self.asset
        if self.details:
            payload["details"] = dict(self.details)
        return payload


def app_error(
    code: str,
    message: str,
    *,
    asset: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> M1AppError:
    return M1AppError(code, message, asset=asset, details=details)
