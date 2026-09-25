"""The small model contract. Use the full CadQuery API inside build(parameters)."""

from dataclasses import dataclass
from typing import Any


@dataclass
class Feature:
    name: str
    shape: Any
    operation: str = "feature"
    description: str = ""
    id: str | None = None


@dataclass
class Model:
    features: list[Feature]

    @property
    def result(self):
        if not self.features:
            raise ValueError("Return a Model with at least one solid feature.")
        return self.features[-1].shape
