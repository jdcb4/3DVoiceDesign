"""Human-readable design files, atomic saves, and portable source checkpoints."""

import hashlib
import json
import math
import re
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}$")
BLANK_SOURCE = '''"""Blank design. Add named solid features in build(p). Dimensions are in mm."""

from voicedesign.cad import Model


def build(p):
    return Model([])
'''


class Parameter(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    label: str
    value: float
    min: float
    max: float
    step: float = Field(default=1, gt=0)
    unit: str = "mm"
    description: str = ""

    @model_validator(mode="after")
    def check_range(self):
        if not self.min <= self.value <= self.max:
            raise ValueError(f"{self.label} must be between {self.min} and {self.max}.")
        return self


class Design(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(default=1, ge=1, le=1)
    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    units: str = Field(default="mm", pattern="^mm$")
    parameters: dict[str, Parameter]
    # None keeps the original trusted-Python workflow; a list is the editable feature graph.
    features: list[dict] | None = None
    print_notes: str = "Review orientation, walls, and clearances before printing."


def atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def document_text(document: Design):
    return document.model_dump_json(indent=2) + "\n"


def revision_for(raw: str, source: str):
    return hashlib.sha256((raw + "\0" + source).encode()).hexdigest()[:24]


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def path(self, design_id: str):
        if not ID.fullmatch(design_id):
            raise ValueError("Invalid design identifier.")
        path = (self.root / design_id).resolve()
        if path.parent != self.root:
            raise ValueError("Design must be inside the designs folder.")
        return path

    def read(self, design_id: str):
        with self.lock:
            path = self.path(design_id)
            raw = (path / "design.json").read_text(encoding="utf-8-sig")
            source = (path / "model.py").read_text(encoding="utf-8-sig")
            document = Design.model_validate_json(raw)
            fingerprint = revision_for(raw, source)
            # Editing the local source is an explicit escape into trusted Python. Never
            # silently ignore an agent/human's source changes because a graph still exists.
            if document.features is not None and source != BLANK_SOURCE:
                document.features = None
            return document, source, fingerprint

    def commit_document(self, design_id: str, document: Design, expected: str, label: str):
        """Commit an already-validated candidate, preserving one prior checkpoint."""
        with self.lock:
            current, source, revision = self.read(design_id)
            if revision != expected:
                raise Conflict("Design changed while this edit was building. Reload and reconcile.")
            if document == current:
                return revision
            self.checkpoint(design_id, label)
            raw = document_text(document)
            atomic_write(self.path(design_id) / "design.json", raw)
            return revision_for(raw, source)

    def list(self):
        items = []
        for path in sorted(self.root.iterdir()):
            if path.is_dir() and (path / "design.json").exists():
                try:
                    doc, _, _ = self.read(path.name)
                    items.append(
                        {"id": path.name, "name": doc.name, "description": doc.description}
                    )
                except (ValueError, OSError) as error:
                    items.append({"id": path.name, "name": path.name, "error": str(error)})
        return items

    def checkpoint(self, design_id: str, label: str = "Saved checkpoint"):
        with self.lock:
            doc, source, fingerprint = self.read(design_id)
            # Archive the persisted graph even when a local source override makes
            # the effective runtime mode Python. It may be the only copy of it.
            doc = Design.model_validate_json(
                (self.path(design_id) / "design.json").read_text(encoding="utf-8-sig")
            )
            revision = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8]
            data = {
                "id": revision,
                "label": label[:160],
                "created_at": datetime.now(UTC).isoformat(),
                "fingerprint": fingerprint,
                "design": doc.model_dump(),
                "source": source,
            }
            atomic_write(
                self.path(design_id) / "history" / f"{revision}.json", json.dumps(data, indent=2)
            )
            return {k: v for k, v in data.items() if k not in ("design", "source")}

    def history(self, design_id: str):
        self.read(design_id)
        result = []
        for path in sorted((self.path(design_id) / "history").glob("*.json"), reverse=True):
            data = json.loads(path.read_text(encoding="utf-8"))
            result.append({k: data[k] for k in ("id", "label", "created_at", "fingerprint")})
        return result

    def update_parameters(self, design_id: str, values: dict, expected: str):
        with self.lock:
            doc, _, fingerprint = self.read(design_id)
            if expected != fingerprint:
                raise Conflict(
                    "Design changed on disk. Reload the current parameters before applying."
                )
            if set(values) - set(doc.parameters):
                raise ValueError("Unknown parameter name.")
            data = doc.model_dump()
            for key, value in values.items():
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (float, int))
                    or not math.isfinite(value)
                ):
                    raise ValueError(f"{key} must be a finite number.")
                data["parameters"][key]["value"] = value
            updated = Design.model_validate(data)
            if updated != doc:
                self.checkpoint(design_id, "Before parameter edit")
                atomic_write(
                    self.path(design_id) / "design.json", updated.model_dump_json(indent=2) + "\n"
                )

    def restore(self, design_id: str, revision: str, expected: str):
        with self.lock:
            if not ID.fullmatch(revision):
                raise ValueError("Invalid checkpoint identifier.")
            _, _, fingerprint = self.read(design_id)
            if fingerprint != expected:
                raise Conflict("Design changed on disk. Reload before restoring.")
            data = json.loads(
                (self.path(design_id) / "history" / f"{revision}.json").read_text(encoding="utf-8")
            )
            doc = Design.model_validate(data["design"])
            self.checkpoint(design_id, "Before restoring checkpoint")
            atomic_write(self.path(design_id) / "model.py", data["source"])
            atomic_write(self.path(design_id) / "design.json", doc.model_dump_json(indent=2) + "\n")

    def create(self, name: str, template: Path | None = None):
        with self.lock:
            name = name.strip()
            if not name or len(name) > 100:
                raise ValueError("Use a design name of 1–100 characters.")
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "design"
            design_id = slug
            while self.path(design_id).exists():
                design_id = slug + "-" + uuid.uuid4().hex[:6]
            if template is None:
                doc = Design(
                    name=name,
                    description="A blank design, ready for your first feature.",
                    parameters={},
                    features=[],
                )
                source = BLANK_SOURCE
            else:
                doc = Design.model_validate_json(
                    (template / "design.json").read_text(encoding="utf-8-sig")
                )
                source = (template / "model.py").read_text(encoding="utf-8-sig")
            doc.name = name
            path = self.path(design_id)
            path.mkdir()
            atomic_write(path / "model.py", source)
            atomic_write(path / "design.json", doc.model_dump_json(indent=2) + "\n")
            self.checkpoint(design_id, "Design created")
            return design_id


class Conflict(ValueError):
    pass
