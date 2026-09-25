"""Isolated sample workspace for browser tests; never writes the user's designs."""

import tempfile
from pathlib import Path

import uvicorn

from voicedesign.engine import ROOT
from voicedesign.server import create_app
from voicedesign.storage import Store

if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="voicedesign-ui-") as directory:
        root = Path(directory)
        store = Store(root / "designs")
        for template in ("mounting-plate", "enclosure", "turned-knob"):
            store.create(template.replace("-", " ").title(), ROOT / "templates" / template)
        uvicorn.run(
            create_app(root / "designs", root / "cache"),
            host="127.0.0.1",
            port=8744,
            log_level="warning",
        )
