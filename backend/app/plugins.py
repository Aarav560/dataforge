"""Custom source connectors loaded as plugins.

A plugin is a Python module that defines `Connector` subclasses (or lists them
in `CONNECTORS`). DataForge loads plugins from:

- installed packages exposing the entry point group `dataforge.connectors`
- `*.py` files in PLUGINS_DIR (default: the repository's `plugins/` folder)

Plugins run with the server's permissions, so only install ones you trust.
Constructors receive `(client, settings)`.
"""

import importlib.util
import inspect
import logging
import sys
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path

from .config import Settings, get_settings
from .connectors.base import Connector

log = logging.getLogger(__name__)
ENTRY_POINT_GROUP = "dataforge.connectors"


@dataclass
class PluginInfo:
    name: str
    origin: str
    kinds: list[str] = field(default_factory=list)
    description: str = ""
    error: str | None = None
    cls: type | None = None


_loaded: dict[str, PluginInfo] | None = None


def _classes(module) -> list[type]:
    listed = getattr(module, "CONNECTORS", None)
    if listed is not None:
        return list(listed)
    return [obj for _, obj in inspect.getmembers(module, inspect.isclass)
            if issubclass(obj, Connector) and obj is not Connector and obj.__module__ == module.__name__]


def _register(found: dict[str, PluginInfo], cls: type, origin: str, builtin: set[str]) -> None:
    from .planner import KINDS

    name = getattr(cls, "name", None)
    kinds = sorted(getattr(cls, "kinds", ()) or ())
    info = PluginInfo(name=str(name), origin=origin, kinds=kinds,
                      description=(inspect.getdoc(cls) or "").split("\n")[0])
    if not isinstance(name, str) or not name.isidentifier():
        info.error = "connector needs a `name` that is a valid identifier"
    elif name in builtin:
        info.error = f"name {name!r} clashes with a built-in source"
    elif name in found and found[name].error is None:
        info.error = f"duplicate connector name {name!r} (also in {found[name].origin})"
    elif not kinds or not set(kinds) <= set(KINDS):
        info.error = f"kinds must be a non-empty subset of {list(KINDS)}"
    else:
        info.cls = cls
    if info.error:
        log.warning("plugin %s from %s rejected: %s", name, origin, info.error)
    found[info.name if info.error is None else f"{info.name}@{origin}"] = info


def load(settings: Settings | None = None, force: bool = False) -> dict[str, PluginInfo]:
    global _loaded
    if _loaded is not None and not force:
        return _loaded
    from .connectors import SOURCES

    settings = settings or get_settings()
    builtin = set(SOURCES)
    found: dict[str, PluginInfo] = {}
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        try:
            obj = ep.load()
            classes = [obj] if inspect.isclass(obj) else _classes(obj)
            for cls in classes:
                _register(found, cls, f"entry point {ep.name}", builtin)
        except Exception as exc:
            found[f"{ep.name}@entry-point"] = PluginInfo(ep.name, f"entry point {ep.name}", error=str(exc))
    directory = settings.plugins_dir
    if directory and directory.is_dir():
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            mod_name = f"dataforge_plugin_{path.stem}"
            try:
                spec = importlib.util.spec_from_file_location(mod_name, path)
                module = importlib.util.module_from_spec(spec)
                sys.modules[mod_name] = module
                spec.loader.exec_module(module)
                for cls in _classes(module):
                    _register(found, cls, str(path.name), builtin)
            except Exception as exc:
                log.warning("plugin file %s failed to load: %s", path, exc)
                found[f"{path.stem}@{path.name}"] = PluginInfo(path.stem, path.name, error=f"{type(exc).__name__}: {exc}")
    _loaded = found
    _extend_planner(found)
    return found


def _extend_planner(found: dict[str, PluginInfo]) -> None:
    """Route needs of a plugin's kinds to it too."""
    from . import planner

    for info in found.values():
        if info.cls is None:
            continue
        for kind in info.kinds:
            if info.name not in planner.KIND_SOURCES[kind]:
                planner.KIND_SOURCES[kind] = (*planner.KIND_SOURCES[kind], info.name)


def connectors(client, settings: Settings) -> dict[str, Connector]:
    out = {}
    for info in load(settings).values():
        if info.cls is None:
            continue
        try:
            out[info.name] = info.cls(client, settings)
        except Exception as exc:
            log.warning("plugin %s failed to start: %s", info.name, exc)
    return out
