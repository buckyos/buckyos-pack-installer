"""Common helpers for local BuckyOS packaging scripts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]


def require_yaml() -> Any:
    if yaml is None:
        raise ImportError(
            "PyYAML is required to read bucky_project.yaml. "
            "Use your project venv or install via `pip install pyyaml`."
        )
    return yaml


def yaml_load_file(path: Path) -> dict[str, Any]:
    data = require_yaml().safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a map: {path}")
    return data


def json_load_file(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON root must be a map: {path}")
    return data


def parse_bool(value: Any, *, field_name: str, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "yes", "1", "on"):
            return True
        if normalized in ("false", "no", "0", "off"):
            return False
    raise ValueError(f"{field_name} must be a boolean value")


def parse_component_type(value: Any, *, field_name: str) -> str:
    kind = str(value or "app").strip()
    if kind not in ("app", "bundle"):
        raise ValueError(f"{field_name} must be one of: app, bundle")
    return kind


def canonical_arch(raw_arch: str) -> str:
    arch = (raw_arch or "").strip().lower()
    if arch in ("x86_64", "amd64"):
        return "amd64"
    if arch in ("arm64", "aarch64"):
        return "arm64"
    raise ValueError(f"unsupported architecture: {raw_arch}")


def package_filename(*, platform_key: str, architecture: str, version: str, package_format: str) -> str:
    arch = canonical_arch(architecture)
    if platform_key == "windows" and package_format == "exe":
        return f"buckyos-windows-{arch}-{version}.exe"
    if platform_key == "macos" and package_format == "pkg":
        return f"buckyos-macos-{arch}-{version}.pkg"
    if platform_key == "linux" and package_format in ("deb", "rpm"):
        return f"buckyos-linux-{arch}-{version}.{package_format}"
    raise ValueError(f"unsupported package target: {platform_key}/{package_format}")


def manifest_install_project(manifest_path: Path, project_key: str) -> dict[str, Any]:
    data = json_load_file(manifest_path)
    install_projects = data.get("install_projects", {}) or {}
    if not isinstance(install_projects, dict):
        raise ValueError("manifest.install_projects must be a map")
    project = install_projects.get(project_key)
    if not isinstance(project, dict):
        raise ValueError(f"manifest.install_projects.{project_key} missing or invalid")
    return project


def manifest_component_keys(manifest_path: Path, platform_key: str) -> list[str]:
    data = json_load_file(manifest_path)
    platforms = data.get("platforms", {}) or {}
    platform_cfg = platforms.get(platform_key, {}) or {}
    component_keys = platform_cfg.get("component_keys", []) or []
    if not isinstance(component_keys, list):
        raise ValueError(f"manifest.platforms.{platform_key}.component_keys must be a list")
    return [str(key) for key in component_keys]


def item_paths(project: dict[str, Any], item_name: str, *, project_key: str) -> list[str]:
    items = project.get(item_name, []) or []
    if not isinstance(items, list):
        raise ValueError(f"manifest install project '{project_key}'.{item_name} must be a list")
    return [
        str(item.get("raw_path") or item.get("target_dir_name") or "")
        for item in items
        if isinstance(item, dict) and str(item.get("raw_path") or item.get("target_dir_name") or "").strip()
    ]


def item_source_paths(project: dict[str, Any], item_name: str, *, project_key: str) -> dict[str, str]:
    items = project.get(item_name, []) or []
    if not isinstance(items, list):
        raise ValueError(f"manifest install project '{project_key}'.{item_name} must be a list")
    out: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        rel = str(item.get("raw_path") or item.get("target_dir_name") or "").strip()
        source_path = str(item.get("source_path") or "").strip()
        if rel and source_path:
            out[rel] = source_path
    return out


def discover_component_hook(
    *,
    scripts_dirs: Iterable[Path],
    component_key: str,
    step: str,
    extensions: Sequence[str],
) -> Path | None:
    for scripts_dir in scripts_dirs:
        for extension in extensions:
            candidate = scripts_dir / f"{component_key}_{step}{extension}"
            if candidate.exists():
                if not candidate.is_file():
                    raise ValueError(f"component hook must be a regular file: {candidate}")
                return candidate
    return None
