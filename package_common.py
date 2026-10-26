"""Common helpers for local BuckyOS packaging scripts."""

from __future__ import annotations

import hashlib
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


def project_load_file(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".json":
        return json_load_file(path)
    return yaml_load_file(path)


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


def package_filename(
    *, platform_key: str, architecture: str, version: str, package_format: str, edition: str = "full"
) -> str:
    arch = canonical_arch(architecture)
    # BuckyOS Desktop Lite is its own package: buckyos-lite-<platform>-...
    product = "buckyos" if edition == "full" else f"buckyos-{edition}"
    if platform_key == "windows" and package_format == "exe":
        return f"{product}-windows-{arch}-{version}.exe"
    if platform_key == "macos" and package_format == "pkg":
        return f"{product}-macos-{arch}-{version}.pkg"
    if platform_key == "linux" and package_format in ("deb", "rpm"):
        return f"{product}-linux-{arch}-{version}.{package_format}"
    raise ValueError(f"unsupported package target: {platform_key}/{package_format}")


def staged_product_profile(staged_root: Path) -> dict[str, Any]:
    """etc/product.json of a staged BuckyOS root (make_config --edition);
    the full edition when the root has none."""
    path = staged_root / "etc" / "product.json"
    if not path.is_file():
        return {"edition": "full", "capabilities": {"container": True}}
    profile = json_load_file(path)
    if profile.get("edition") not in ("full", "lite"):
        raise ValueError(f"{path}: unknown product edition {profile.get('edition')!r}")
    return profile


def staged_product_edition(staged_root: Path) -> str:
    return str(staged_product_profile(staged_root)["edition"])


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


def normalize_item_relpath(rel: str, *, windows: bool = False) -> str:
    rel_s = rel.strip()
    if windows:
        rel_s = rel_s.lstrip("/\\").rstrip("/\\")
    else:
        rel_s = rel_s.lstrip("/").rstrip("/")
    return rel_s


def source_path_for(
    *,
    source_rootfs: Path,
    rel: str,
    item_source_paths: dict[str, str],
    source_root_override: Path | None = None,
    windows: bool = False,
) -> Path:
    def existing_or_windows_launcher(path: Path) -> Path | None:
        if path.exists():
            return path
        if windows and not path.suffix:
            for suffix in (".ps1", ".cmd", ".exe"):
                launcher_path = path.with_name(path.name + suffix)
                if launcher_path.exists():
                    return launcher_path
        return None

    override_rel = normalize_item_relpath(rel, windows=windows)
    if source_root_override is not None:
        candidate = source_root_override / override_rel
        existing = existing_or_windows_launcher(candidate)
        if existing is not None:
            return existing
    configured = item_source_paths.get(rel)
    if configured:
        configured_path = Path(configured).resolve()
        existing = existing_or_windows_launcher(configured_path)
        if existing is not None:
            return existing
        return configured_path
    fallback = source_rootfs / override_rel
    existing = existing_or_windows_launcher(fallback)
    if existing is not None:
        return existing
    return fallback


def normalize_payload_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/").lstrip("./").lstrip("/").rstrip("/")
    parts = [part for part in normalized.split("/") if part and part != "."]
    return "/".join(parts)


def payload_path_matches_prefix(path: str, prefix: str) -> bool:
    normalized_path = normalize_payload_path(path)
    normalized_prefix = normalize_payload_path(prefix)
    if not normalized_path or not normalized_prefix:
        return normalized_path == normalized_prefix
    return normalized_path == normalized_prefix or normalized_path.startswith(normalized_prefix + "/")


def unexpected_payload_paths(
    payload_paths: Iterable[str],
    *,
    allowed_prefixes: Iterable[str],
    allowed_exact: Iterable[str] = (),
) -> list[str]:
    prefixes = [normalize_payload_path(prefix) for prefix in allowed_prefixes if normalize_payload_path(prefix)]
    exact = {normalize_payload_path(path) for path in allowed_exact}
    unexpected: list[str] = []
    for payload_path in payload_paths:
        normalized = normalize_payload_path(payload_path)
        if not normalized or normalized in exact:
            continue
        if any(payload_path_matches_prefix(normalized, prefix) for prefix in prefixes):
            continue
        unexpected.append(normalized)
    return sorted(set(unexpected))


def unexpected_data_payload_paths(
    payload_paths: Iterable[str],
    *,
    target_root: str,
    data_path: str,
    module_paths: Iterable[str],
) -> list[str]:
    """Return real data-path payload entries not covered by explicit modules.

    A module may intentionally live below a broader persistent ``data_path``.
    Its payload file (or directory tree) and the directory entries leading to it
    are valid overwrite payload. Other entries below the data path must remain
    in the installer defaults area.
    """
    normalized_root = normalize_payload_path(target_root)
    normalized_data_path = normalize_item_relpath(data_path, windows=True)
    data_prefix = normalize_payload_path(f"{normalized_root}/{normalized_data_path}")
    if not data_prefix:
        return []

    module_prefixes: list[str] = []
    for module_path in module_paths:
        normalized_module_path = normalize_item_relpath(module_path, windows=True)
        if not normalized_module_path:
            continue
        module_prefix = normalize_payload_path(f"{normalized_root}/{normalized_module_path}")
        if payload_path_matches_prefix(module_prefix, data_prefix):
            module_prefixes.append(module_prefix)

    unexpected: list[str] = []
    for payload_path in payload_paths:
        normalized = normalize_payload_path(payload_path)
        if not normalized or not payload_path_matches_prefix(normalized, data_prefix):
            continue
        if any(
            payload_path_matches_prefix(normalized, module_prefix)
            or payload_path_matches_prefix(module_prefix, normalized)
            for module_prefix in module_prefixes
        ):
            continue
        unexpected.append(normalized)
    return sorted(set(unexpected))


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


PAIOS_IMAGES_MANIFEST = "paios_images.json"


def package_arch_from_name(file_name: str) -> str:
    """Architecture from a package_filename() style name, e.g. buckyos-macos-arm64-<version>.pkg."""
    for part in file_name.split("-"):
        if part.lower() in ("amd64", "x86_64", "arm64", "aarch64"):
            return canonical_arch(part)
    raise ValueError(f"cannot tell the architecture of {file_name}")


def paios_images_arch(raw_arch: str) -> str:
    """Architecture key used by bin/paios-images/paios_images.json."""
    return "aarch64" if canonical_arch(raw_arch) == "arm64" else "amd64"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_paios_images(extract_root: Path, raw_arch: str, failures: list[str], *, required: bool) -> None:
    """Check bundled paios image tarballs against bin/paios-images/paios_images.json.

    Release payloads pin the worker/ExtTool images; node_daemon never pulls them and
    loads a missing image from the tarball next to the manifest. Files are located by
    name because some extractors (NSIS) flatten directories.
    """
    manifests = [path for path in extract_root.rglob(PAIOS_IMAGES_MANIFEST) if path.is_file()]
    if not manifests:
        if required:
            failures.append(f"payload has no bin/paios-images/{PAIOS_IMAGES_MANIFEST}")
        return
    if len(manifests) > 1:
        failures.append(f"payload has {len(manifests)} copies of {PAIOS_IMAGES_MANIFEST}")
        return
    try:
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        images = manifest["images"]
        if manifest.get("schema") != "buckyos.paios-images/v1" or "aios" not in images:
            raise ValueError("unsupported schema or missing images.aios")
    except (OSError, ValueError, KeyError, TypeError) as error:
        failures.append(f"invalid {PAIOS_IMAGES_MANIFEST}: {error}")
        return
    arch = paios_images_arch(raw_arch)
    for name, image in images.items():
        entry = (image.get("platforms") or {}).get(arch) if isinstance(image, dict) else None
        if not isinstance(entry, dict) or not isinstance(entry.get("tar"), dict):
            failures.append(f"{PAIOS_IMAGES_MANIFEST} has no {arch} entry for {name}")
            continue
        tar = entry["tar"]
        file_name = str(tar.get("file", ""))
        if not file_name or "/" in file_name or "\\" in file_name or ".." in file_name:
            failures.append(f"{PAIOS_IMAGES_MANIFEST} {name}.{arch} has an invalid tar.file {file_name!r}")
            continue
        candidate = manifests[0].parent / file_name
        if not candidate.is_file():
            matches = [path for path in extract_root.rglob(file_name) if path.is_file()]
            candidate = matches[0] if len(matches) == 1 else None
        if candidate is None:
            if required:
                failures.append(f"payload is missing paios image tarball {file_name} ({name} {arch})")
            continue
        size = candidate.stat().st_size
        if size != tar.get("size") or _sha256_file(candidate) != tar.get("sha256"):
            failures.append(f"paios image tarball {file_name} does not match {PAIOS_IMAGES_MANIFEST}")
        else:
            print(f"[verify] paios image {name} {arch}: {file_name} ({size / (1024 * 1024):.1f} MB) OK")
