"""P3C API DTOs.

These models are HTTP contracts only. They intentionally do not alter APP or SP
processing versions.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApiEnvelope(BaseModel):
    api_version: str


class SessionSummary(ApiEnvelope):
    session_id: str
    source_type: str
    completed: bool
    completion_reason: str | None
    raw_persistence_status: str
    app_registered: bool
    committed_run_count: int
    current_run_id: str | None


class SessionsResponse(ApiEnvelope):
    sessions: list[SessionSummary]


class SessionDetail(SessionSummary):
    sample_rate_hz: float
    configured_channels: list[str]
    started_at_utc: str
    ended_at_utc: str | None
    parameter_status: str
    formal_parameters: None = None
    formal_parameters_allowed: bool = False
    limitations: list[str]
    raw_integrity_assurance: str | None
    source_assets: list[dict[str, Any]]
    runs: list[dict[str, Any]]


class SeriesMetadata(BaseModel):
    original_count: int
    returned_count: int
    downsampled: bool
    downsampling: str = "display-only"


class ChannelSeries(BaseModel):
    name: str
    values: list[Any]
    metadata: SeriesMetadata


class ChannelsResponse(ApiEnvelope):
    session_id: str
    run_id: str | None
    raw: dict[str, ChannelSeries]
    processed: dict[str, ChannelSeries] = Field(default_factory=dict)


class AnalysisResponse(ApiEnvelope):
    session_id: str
    run_id: str
    analysis: dict[str, Any]


class ReportResponse(ApiEnvelope):
    session_id: str
    run_id: str
    persisted: bool
    report: dict[str, Any]


class RunSummary(BaseModel):
    run_id: str
    state: str
    committed_at_utc: str
    execution_mode: str
    asset_roles: list[str]


class RunsResponse(ApiEnvelope):
    session_id: str
    current_run_id: str | None
    runs: list[RunSummary]


class RunDetail(ApiEnvelope):
    session_id: str
    run_id: str
    run: dict[str, Any]
    assets: list[dict[str, Any]]


class ReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persist: bool = False
    run_id: str | None = None
    software_commit_sha: str = Field(default="0" * 40, pattern=r"^[0-9a-f]{40}$")


class ReplayResponse(ApiEnvelope):
    session_id: str
    run_id: str | None
    persisted: bool
    sp_result_sha256: str
    analysis: dict[str, Any]
