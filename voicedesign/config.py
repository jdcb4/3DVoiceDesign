"""Resolve writable workspaces independently of the installed application."""

import hashlib
import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
TEMPLATES = PACKAGE / "templates"
STATIC = PACKAGE / "static"


def user_home():
    if value := os.environ.get("VOICEDESIGN_HOME"):
        return Path(value).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return base / "VoiceDesign3D"


def read_config(path):
    return tomllib.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}


def project_config(start=None):
    current = Path(start or Path.cwd()).resolve()
    for parent in (current, *current.parents):
        path = parent / ".voicedesign.toml"
        if path.is_file():
            return path, read_config(path)
    return None, {}


@dataclass(frozen=True)
class Workspace:
    root: Path

    @property
    def designs(self):
        return self.root / "designs"

    @property
    def exports(self):
        return self.root / "exports"

    @property
    def runtime(self):
        key = hashlib.sha256(os.path.normcase(str(self.root)).encode()).hexdigest()[:24]
        return user_home() / "runtime" / key

    @property
    def cache(self):
        return self.runtime / "cache"


def resolve_workspace(explicit=None, start=None):
    path, config = project_config(start)
    if explicit:
        root = Path(explicit)
    elif config.get("workspace"):
        root = path.parent / config["workspace"]
    elif value := os.environ.get("VOICEDESIGN_WORKSPACE"):
        root = Path(value)
    else:
        settings = read_config(user_home() / "config.toml")
        root = Path(settings.get("default_workspace", user_home() / "workspaces/default"))
        if not root.is_absolute():
            root = user_home() / root
    root = root.expanduser().resolve()
    if root == PACKAGE or PACKAGE in root.parents:
        raise ValueError("Workspaces must be outside the installed application package.")
    return Workspace(root)
