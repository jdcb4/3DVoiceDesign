"""Small, deterministic, revision-checked edits to saved feature documents."""

import copy
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from voicedesign.storage import Design, Parameter


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Add(StrictModel):
    op: Literal["add"]
    feature: dict
    index: int | None = Field(default=None, ge=0)


class Changes(StrictModel):
    name: str | None = Field(default=None, max_length=100)
    params: dict | None = None
    replace_params: bool = False
    suppressed: bool | None = None


class Update(StrictModel):
    op: Literal["update"]
    id: str
    changes: Changes


class Remove(StrictModel):
    op: Literal["remove"]
    id: str


class Move(StrictModel):
    op: Literal["move"]
    id: str
    index: int = Field(ge=0)


class SetParameters(StrictModel):
    op: Literal["set_parameters"]
    values: dict[str, float]


class DefineParameter(StrictModel):
    op: Literal["define_parameter"]
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    parameter: Parameter


class RemoveParameter(StrictModel):
    op: Literal["remove_parameter"]
    name: str


Operation = Annotated[
    Add | Update | Remove | Move | SetParameters | DefineParameter | RemoveParameter,
    Field(discriminator="op"),
]


class AgentTiming(StrictModel):
    # Caller-reported observations, never fabricated from API elapsed time.
    planning_ms: float | None = Field(default=None, ge=0, le=86_400_000)
    tool_dispatch_ms: float | None = Field(default=None, ge=0, le=86_400_000)


class BatchRequest(StrictModel):
    expected_revision: str = Field(min_length=1, max_length=96)
    operations: list[Operation] = Field(min_length=1, max_length=128)
    label: str = Field(default="Feature edit", max_length=140)
    actor: Literal["human", "agent"] = "agent"
    trace_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")
    agent_timing: AgentTiming | None = None


def apply_operations(document: Design, operations: list[Operation]):
    """Prepare a new document in memory. Geometry and persistence happen later."""
    from voicedesign.features import validate_features

    data = copy.deepcopy(document.model_dump())
    if data["features"] is None and any(
        not isinstance(op, (SetParameters, DefineParameter, RemoveParameter)) for op in operations
    ):
        raise ValueError(
            "This design uses custom Python. Edit its source or create a structured blank design."
        )
    features = data["features"]
    for op in operations:
        if isinstance(op, Add):
            index = len(features) if op.index is None else op.index
            if index > len(features):
                raise ValueError("Insert index is outside the feature list.")
            features.insert(index, copy.deepcopy(op.feature))
        elif isinstance(op, (Update, Remove, Move)):
            matches = [i for i, f in enumerate(features) if f.get("id") == op.id]
            if len(matches) != 1:
                raise ValueError(f"Feature {op.id!r} does not exist uniquely.")
            index = matches[0]
            if isinstance(op, Remove):
                features.pop(index)
            elif isinstance(op, Move):
                if op.index >= len(features):
                    raise ValueError("Move index is outside the feature list.")
                features.insert(op.index, features.pop(index))
            else:
                changes = op.changes
                if changes.name is not None:
                    features[index]["name"] = changes.name
                if changes.suppressed is not None:
                    features[index]["suppressed"] = changes.suppressed
                if changes.params is not None:
                    features[index]["params"] = (
                        copy.deepcopy(changes.params)
                        if changes.replace_params
                        else {**features[index].get("params", {}), **copy.deepcopy(changes.params)}
                    )
        elif isinstance(op, SetParameters):
            unknown = set(op.values) - set(data["parameters"])
            if unknown:
                raise ValueError("Unknown parameters: " + ", ".join(sorted(unknown)))
            for key, value in op.values.items():
                data["parameters"][key]["value"] = value
        elif isinstance(op, DefineParameter):
            if op.name in data["parameters"]:
                raise ValueError("Parameter already exists; use set_parameters for its value.")
            data["parameters"][op.name] = op.parameter.model_dump()
        elif isinstance(op, RemoveParameter):
            if op.name not in data["parameters"]:
                raise ValueError("Unknown parameter.")
            del data["parameters"][op.name]
    updated = Design.model_validate(data)
    if updated.features is not None:
        updated.features = validate_features(
            updated.features, {k: p.value for k, p in updated.parameters.items()}
        )
    return updated
