"""Public API v1 response contracts. Dynamic CAD parameters remain JSON objects."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from voicedesign.storage import Design, Parameter


class Contract(BaseModel):
    model_config = ConfigDict(extra="allow")


class ErrorResponse(Contract):
    code: str
    detail: str | list[dict[str, Any]]


ERRORS = {
    code: {"model": ErrorResponse, "description": description}
    for code, description in {
        400: "Invalid host or request",
        403: "Local-origin or lifecycle authorization rejected",
        404: "Resource not found",
        409: "Stale revision or already-used trace ID",
        422: "Invalid request or failed geometry; inspect detail and optional feature_errors",
        500: "Unexpected failure; inspect current state before retrying a write",
    }.items()
}


class Health(Contract):
    status: Literal["ok"]
    app: Literal["voicedesign3d"]
    version: str
    api_version: str
    units: Literal["mm"]
    workspace_path: str
    instance_id: str | None


class ActiveDesign(BaseModel):
    active_design: str | None
    revision: int


class WorkspaceInfo(ActiveDesign):
    workspace_path: str
    designs_path: str
    exports_path: str


class DesignSummary(Contract):
    id: str
    name: str


class CreatedDesign(BaseModel):
    id: str


class Selection(BaseModel):
    revision: str
    feature_id: str | None
    point: list[float] | None
    normal: list[float] | None
    actor: str | None


class FeatureDefinition(Contract):
    id: str
    type: str
    params: dict[str, Any]


class FeatureError(Contract):
    feature_id: str | None = None
    message: str


class FeatureGraph(BaseModel):
    revision: str
    mode: Literal["structured", "python"]
    features: list[FeatureDefinition] | None
    parameters: dict[str, Parameter]
    selection: Selection


class SolidInfo(BaseModel):
    index: int
    id: str | None
    name: str
    operation: str
    description: str
    solids: int
    volume: float = Field(description="Volume in cubic millimeters")
    bounds: list[float] = Field(description="X, Y, Z extents in millimeters")


class BuildResult(BaseModel):
    features: list[SolidInfo]
    stats: SolidInfo | None
    empty: bool


class DesignState(Contract):
    id: str
    design: Design
    source_path: str
    fingerprint: str
    status: Literal["building", "ready", "error"]
    result: BuildResult | None
    error: str | None
    seconds: float | None
    timings: dict[str, Any]
    feature_errors: list[FeatureError]


class BatchResult(BaseModel):
    ok: Literal[True]
    id: str
    revision: str
    trace_id: str
    features: list[FeatureDefinition] | None
    feature_errors: list[FeatureError]
    body_count: int
    bounds: list[float] | None
    volume: float
    timings: dict[str, Any]
    status: Literal["ready"]
    changed: bool


class BatchError(ErrorResponse):
    ok: Literal[False]
    revision: str
    trace_id: str
    feature_errors: list[FeatureError]
    timings: dict[str, Any]


class Source(BaseModel):
    source: str
    design: Design
    fingerprint: str


class Checkpoint(BaseModel):
    id: str
    label: str
    created_at: str
    fingerprint: str


class AgentContext(Contract):
    app: str
    version: str
    api_version: str
    design_name: str
    design_id: str
    workspace_path: str
    api_url: str
    design_url: str
    source_path: str
    manifest_path: str
    revision: str
    mode: Literal["structured", "python"]
    selection: Selection
    instructions: str


class Capabilities(Contract):
    version: int
    api_version: str
    units: str
    operations: dict[str, dict[str, Any]]
    batch_endpoint: str
    batch_schema_url: str
    batch_operations: list[str]
    revision_policy: str
    custom_code: str


class Trace(Contract):
    trace_id: str
    design_id: str
    actor: str
    received_at: str
    expected_revision: str
    operation_count: int
    status: Literal["preparing", "committed", "conflict", "rejected", "error"]


class Okay(BaseModel):
    ok: Literal[True]


class Mesh(SolidInfo):
    positions: list[float]
    indices: list[int]
    edges: list[float]
