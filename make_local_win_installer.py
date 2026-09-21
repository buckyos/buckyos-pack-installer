"""
Windows NSIS Installer Builder for BuckyOS.

This script supports:
- build-pkg:   Build a Windows .exe installer using NSIS
- verify-pkg:  Verify a built installer using 7zip
- verify:      Alias of verify-pkg for backward compatibility
- sync:        Regenerate PowerShell scripts from bucky_project.yaml

It reads:
- `apps.buckyos.*` for app layout configuration.
- `publish.win_pkg.apps.*` for Windows distribution package components.

Before building, ensure you have built the latest components:
- buckyos-build && buckyos-install --all --target-rootfs=C:\\opt\\buckyosci\\buckyos
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import package_common as common

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]


SRC_DIR = Path(__file__).resolve().parent.parent
PROJECT_YAML = SRC_DIR / "bucky_project.yaml"

RESULT_ROOT_DIR = Path(os.environ.get("BUCKYOS_BUILD_ROOT", "C:\\opt\\buckyosci"))
TMP_INSTALL_DIR = RESULT_ROOT_DIR / "win-installer"

WIN_PKG_PROJECT_DIR = SRC_DIR / "publish" / "win_pkg"
BUCKYOS_DEFAULTS_SUBDIR = ".buckyos_installer_defaults"
IGNORED_STAGE_NAMES = {".DS_Store", "__pycache__"}
WINDOWS_HOOK_EXTENSIONS = (".ps1", ".bat", ".cmd")
WINDOWS_HOOK_STEPS = ("preinstall", "postinstall", "preuninstall")
WINDOWS_SERVICE_SCRIPT_NAMES = (
    "seed_defaults.ps1",
    "ensure_firewall_rules.ps1",
    "uninstall_cleanup.ps1",
    "node_daemon_loader.vbs",
)


def yaml_load_file(path: Path) -> Dict[str, Any]:
    return common.project_load_file(path)


def json_load_file(path: Path) -> Dict[str, Any]:
    return common.json_load_file(path)


def _expand_vars(s: str) -> str:
    """Expand environment variables in path strings."""
    out = s
    for name, default in [
        ("BUCKYOS_ROOT", "C:\\opt\\buckyos"),
        ("BUCKYOS_BUILD_ROOT", str(RESULT_ROOT_DIR)),
        ("APPDATA", os.environ.get("APPDATA") or "%APPDATA%"),
        ("LOCALAPPDATA", os.environ.get("LOCALAPPDATA") or "%LOCALAPPDATA%"),
        ("USERPROFILE", os.environ.get("USERPROFILE") or "%USERPROFILE%"),
    ]:
        val = os.environ.get(name, default)
        out = out.replace(f"${{{name}}}", val)
        out = out.replace(f"%{name}%", val)
    return out


def _manifest_install_project(manifest_path: Path, project_key: str) -> Dict[str, Any]:
    return common.manifest_install_project(manifest_path, project_key)


@dataclass(frozen=True)
class AppLayout:
    source_rootfs: Path
    target_rootfs: Path
    module_paths: List[str]
    data_paths: List[str]
    clean_paths: List[str]
    module_source_paths: Dict[str, str]
    data_source_paths: Dict[str, str]


def _ignore_copy_entries(_: str, names: List[str]) -> List[str]:
    return [name for name in names if name in IGNORED_STAGE_NAMES]


def _copytree_filtered(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, dirs_exist_ok=True, ignore=_ignore_copy_entries)


def _write_nsis_license_rtf(source_path: Path, out_path: Path) -> Path:
    """Convert UTF-8 license text to RTF using built-in Windows PowerShell APIs."""

    source = str(source_path)
    target = str(out_path)
    ps_script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$rtb = New-Object System.Windows.Forms.RichTextBox; "
        f"$rtb.Text = Get-Content -Raw -Encoding UTF8 '{source}'; "
        f"[System.IO.File]::WriteAllText('{target}', $rtb.Rtf, [System.Text.Encoding]::ASCII)"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-STA", "-Command", ps_script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            "failed to convert license.txt to RTF with PowerShell: "
            f"{result.stderr.strip() or result.stdout.strip() or 'unknown error'}"
        )
    return out_path


def load_app_layout(
    project_yaml_path: Path,
    app_key: str,
    target_override: str | None = None,
) -> AppLayout:
    data = yaml_load_file(project_yaml_path)
    apps = data.get("apps", {}) or {}
    if not isinstance(apps, dict):
        raise ValueError("apps must be a map")
    app_cfg = apps.get(app_key)
    if not isinstance(app_cfg, dict):
        raise ValueError(f"apps.{app_key} missing or invalid")

    base_dir = str(data.get("base_dir", "."))
    project_base = (project_yaml_path.parent / base_dir).resolve()

    rootfs_rel = str(app_cfg.get("rootfs", "rootfs/"))
    source_rootfs = (project_base / rootfs_rel).resolve()

    default_target = str(app_cfg.get("default_target_rootfs", "${BUCKYOS_ROOT}"))
    target_str = target_override if target_override else default_target
    target_rootfs = Path(_expand_vars(target_str)).resolve()

    modules = app_cfg.get("modules", {}) or {}
    data_paths_raw = app_cfg.get("data_paths", []) or []
    clean_paths_raw = app_cfg.get("clean_paths", []) or []
    if not isinstance(modules, dict):
        raise ValueError(f"apps.{app_key}.modules must be a map")
    if not isinstance(data_paths_raw, list):
        raise ValueError(f"apps.{app_key}.data_paths must be a list")
    if not isinstance(clean_paths_raw, list):
        raise ValueError(f"apps.{app_key}.clean_paths must be a list")
    module_paths = [str(p) for p in modules.values()]
    data_paths = [str(p) for p in data_paths_raw]
    clean_paths = [str(p) for p in clean_paths_raw]

    return AppLayout(
        source_rootfs=source_rootfs,
        target_rootfs=target_rootfs,
        module_paths=module_paths,
        data_paths=data_paths,
        clean_paths=clean_paths,
        module_source_paths={rel: str((source_rootfs / rel.strip().lstrip("/\\")).resolve()) for rel in module_paths},
        data_source_paths={rel: str((source_rootfs / rel.strip().lstrip("/\\")).resolve()) for rel in data_paths},
    )


def load_app_layout_from_manifest(
    manifest_path: Path,
    app_key: str,
    target_override: str | None = None,
) -> AppLayout:
    app_cfg = _manifest_install_project(manifest_path, app_key)
    source_rootfs_raw = app_cfg.get("source_rootfs")
    if not isinstance(source_rootfs_raw, str) or not source_rootfs_raw.strip():
        raise ValueError(f"manifest install project '{app_key}' missing source_rootfs")

    default_target = str(
        app_cfg.get("default_target_rootfs")
        or app_cfg.get("default_target_rootfs_raw")
        or "${BUCKYOS_ROOT}"
    )
    target_str = target_override if target_override else default_target

    return AppLayout(
        source_rootfs=Path(source_rootfs_raw).resolve(),
        target_rootfs=Path(_expand_vars(target_str)).resolve(),
        module_paths=common.item_paths(app_cfg, "module_items", project_key=app_key),
        data_paths=common.item_paths(app_cfg, "data_items", project_key=app_key),
        clean_paths=common.item_paths(app_cfg, "clean_items", project_key=app_key),
        module_source_paths=common.item_source_paths(app_cfg, "module_items", project_key=app_key),
        data_source_paths=common.item_source_paths(app_cfg, "data_items", project_key=app_key),
    )


def resolve_app_layout(
    *,
    app_key: str,
    project_yaml_path: Path,
    manifest_path: Path | None = None,
    target_override: str | None = None,
) -> AppLayout:
    if manifest_path is not None:
        return load_app_layout_from_manifest(manifest_path, app_key, target_override=target_override)
    return load_app_layout(project_yaml_path, app_key, target_override=target_override)


def load_buckyos_layout(project_yaml_path: Path = PROJECT_YAML, target_override: str | None = None) -> AppLayout:
    return load_app_layout(project_yaml_path, "buckyos", target_override=target_override)


def _as_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v)


def _parse_bool(value: Any, *, field_name: str, default: bool = False) -> bool:
    return common.parse_bool(value, field_name=field_name, default=default)


def _parse_component_type(value: Any, *, field_name: str) -> str:
    return common.parse_component_type(value, field_name=field_name)


def _sanitize_id(s: str) -> str:
    out = []
    for ch in s:
        if ch.isalnum() or ch in (".", "-", "_"):
            out.append(ch.lower())
        else:
            out.append("-")
    cleaned = "".join(out).strip("-")
    return cleaned or "component"


@dataclass(frozen=True)
class PublishComponent:
    key: str
    name: str
    kind: str  # "app" | "bundle"
    optional: bool
    default_selected: bool
    src: str | None
    default_target: str
    system_service: bool


def load_win_pkg_components(project_yaml_path: Path) -> List[PublishComponent]:
    data = yaml_load_file(project_yaml_path)
    publish = data.get("publish", {}) or {}
    win_pkg = publish.get("win_pkg", {}) or {}
    apps = win_pkg.get("apps", {}) or {}
    if not isinstance(apps, dict):
        raise ValueError("publish.win_pkg.apps must be a map")

    components: List[PublishComponent] = []
    for key, cfg in apps.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"publish.win_pkg.apps.{key} must be a map")
        name = _as_str(cfg.get("name", key)).strip() or key
        kind = _parse_component_type(cfg.get("type", "app"), field_name=f"publish.win_pkg.apps.{key}.type")
        optional = _parse_bool(cfg.get("optional", False), field_name=f"publish.win_pkg.apps.{key}.optional")
        default_selected = _parse_bool(
            cfg.get("default_selected", False),
            field_name=f"publish.win_pkg.apps.{key}.default_selected",
        )
        src = _as_str(cfg.get("src", "")).strip() or None
        default_target = _as_str(cfg.get("default_target", "")).strip()
        if not default_target:
            raise ValueError(f"publish.win_pkg.apps.{key} missing default_target")
        system_service = _parse_bool(
            cfg.get("system_service", False),
            field_name=f"publish.win_pkg.apps.{key}.system_service",
        )
        components.append(
            PublishComponent(
                key=_as_str(key),
                name=name,
                kind=kind,
                optional=optional,
                default_selected=default_selected,
                src=src,
                default_target=default_target,
                system_service=system_service,
            )
        )
    return components


def load_win_pkg_components_from_manifest(manifest_path: Path) -> List[PublishComponent]:
    data = json_load_file(manifest_path)
    install_projects = data.get("install_projects", {}) or {}
    component_keys = common.manifest_component_keys(manifest_path, "windows")
    if not isinstance(install_projects, dict):
        raise ValueError("manifest.install_projects must be a map")

    components: List[PublishComponent] = []
    for key in component_keys:
        project = install_projects.get(key)
        if not isinstance(project, dict):
            raise ValueError(f"manifest install project missing for Windows component: {key}")
        platform_cfg = (project.get("platforms", {}) or {}).get("windows")
        if not isinstance(platform_cfg, dict):
            raise ValueError(f"manifest install project '{key}' missing windows platform config")
        system_service = _parse_bool(
            platform_cfg.get("system_service", False),
            field_name=f"manifest.install_projects.{key}.platforms.windows.system_service",
        )
        components.append(
            PublishComponent(
                key=_as_str(platform_cfg.get("key", key)),
                name=_as_str(platform_cfg.get("name", project.get("name", key))).strip() or _as_str(key),
                kind=_parse_component_type(
                    platform_cfg.get("kind", project.get("kind", "app")),
                    field_name=f"manifest.install_projects.{key}.platforms.windows.kind",
                ),
                optional=_parse_bool(
                    platform_cfg.get("optional", False),
                    field_name=f"manifest.install_projects.{key}.platforms.windows.optional",
                ),
                default_selected=_parse_bool(
                    platform_cfg.get("default_selected", False),
                    field_name=f"manifest.install_projects.{key}.platforms.windows.default_selected",
                ),
                src=_as_str(platform_cfg.get("src", "")).strip() or None,
                default_target=_as_str(
                    platform_cfg.get("default_target_raw") or platform_cfg.get("default_target", "")
                ).strip(),
                system_service=system_service,
            )
        )
    return components


def _resolve_component_src(component: PublishComponent, app_publish_dir: Path) -> Path:
    """Resolve the source path for a component."""
    if component.src:
        p = Path(component.src)
        if p.is_absolute():
            return p
        return app_publish_dir / component.key / component.src
    return app_publish_dir / component.key


def _resolve_component_target(component: PublishComponent) -> str:
    """Return the target path (expanded) for a component."""
    return _expand_vars(component.default_target)


def _nsis_default_target(component: PublishComponent) -> str:
    target = component.default_target
    replacements = {
        "%APPDATA%": "$APPDATA",
        "%LOCALAPPDATA%": "$LOCALAPPDATA",
        "%USERPROFILE%": "$PROFILE",
        "${APPDATA}": "$APPDATA",
        "${LOCALAPPDATA}": "$LOCALAPPDATA",
        "${USERPROFILE}": "$PROFILE",
    }
    for raw, nsis in replacements.items():
        target = target.replace(raw, nsis)
    return target


def _copy_dir_contents(src_dir: Path, dst_dir: Path) -> None:
    dst_dir.mkdir(parents=True, exist_ok=True)
    for child in src_dir.iterdir():
        if child.name in IGNORED_STAGE_NAMES:
            continue
        dst = dst_dir / child.name
        if child.is_dir():
            _copytree_filtered(child, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(child, dst)


def _copy_windows_service_scripts(scripts_src: Path, scripts_dst: Path) -> None:
    scripts_dst.mkdir(parents=True, exist_ok=True)
    for script_name in WINDOWS_SERVICE_SCRIPT_NAMES:
        src = scripts_src / script_name
        if not src.exists():
            continue
        if not src.is_file():
            raise ValueError(f"Windows service script must be a regular file: {src}")
        shutil.copy2(src, scripts_dst / script_name)


def _discover_windows_hook(component_key: str, step: str) -> Path | None:
    return common.discover_component_hook(
        scripts_dirs=(WIN_PKG_PROJECT_DIR / "scripts",),
        component_key=component_key,
        step=step,
        extensions=WINDOWS_HOOK_EXTENSIONS,
    )


def _stage_windows_hooks(component_key: str, comp_payload: Path) -> None:
    hooks_dir = comp_payload / "scripts" / "hooks"
    for step in WINDOWS_HOOK_STEPS:
        hook_path = _discover_windows_hook(component_key, step)
        if hook_path is None:
            continue
        hooks_dir.mkdir(parents=True, exist_ok=True)
        dst = hooks_dir / f"{step}{hook_path.suffix.lower()}"
        shutil.copy2(hook_path, dst)


def _windows_payload_hook(comp_payload: Path, step: str) -> Path | None:
    hooks_dir = comp_payload / "scripts" / "hooks"
    for extension in WINDOWS_HOOK_EXTENSIONS:
        candidate = hooks_dir / f"{step}{extension}"
        if candidate.exists():
            return candidate
    return None


def _hook_exec_command(var_name: str, hook_rel: str) -> str:
    var_ref = var_name if var_name.startswith("$") else f"${var_name}"
    hook_path = f"{var_ref}\\{hook_rel}"
    suffix = Path(hook_rel).suffix.lower()
    if suffix == ".ps1":
        return f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{hook_path}"'
    if suffix in (".bat", ".cmd"):
        return f'cmd.exe /c ""{hook_path}""'
    raise ValueError(f"unsupported Windows hook extension: {hook_rel}")


def _iter_payload_file_basenames(root: Path, *, limit: int = 256) -> List[str]:
    names: List[str] = []
    if not root.exists():
        return names
    if root.is_file():
        return [root.name.lower()]

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in IGNORED_STAGE_NAMES for part in rel.parts):
            continue
        names.append(path.name.lower())
        if len(names) >= limit:
            break
    return names


def _append_unique_names(target: List[str], values: List[str]) -> None:
    seen = set(target)
    for value in values:
        normalized = value.strip().lower()
        if not normalized or normalized in seen:
            continue
        target.append(normalized)
        seen.add(normalized)


def _component_expected_filenames(
    *,
    comp: "PublishComponent",
    manifest_path: Path | None,
) -> List[str]:
    expected: List[str] = []

    if comp.src:
        _append_unique_names(expected, [Path(comp.src.replace("\\", "/")).name])

    if comp.key == "buckyos":
        _append_unique_names(
            expected,
            [
                "node_daemon.exe",
                "stop.ps1",
                "seed_defaults.ps1",
                "ensure_firewall_rules.ps1",
                "uninstall_cleanup.ps1",
                "node_daemon_loader.vbs",
            ],
        )

    if manifest_path is None:
        return expected

    try:
        project = _manifest_install_project(manifest_path, comp.key)
    except Exception:
        return expected

    platform_cfg = (project.get("platforms", {}) or {}).get("windows", {}) or {}
    source_path_raw = str(platform_cfg.get("source_path") or "").strip()
    if source_path_raw:
        _append_unique_names(expected, _iter_payload_file_basenames(Path(source_path_raw)))

    for item_name in ("module_items", "data_items"):
        items = project.get(item_name, []) or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            file_items = item.get("file_items", []) or []
            if isinstance(file_items, list):
                _append_unique_names(
                    expected,
                    [Path(str(file_item).replace("\\", "/")).name for file_item in file_items],
                )

    return expected


def _stage_buckyos_app_root(*, src_root: Path, dst_root: Path, layout: AppLayout) -> None:
    """
    Stage buckyos rootfs into dst_root.

    Semantics:
    - modules: always copied into real target paths (will be overwritten by installer)
    - data_paths: copied into `${INSTALL_DIR}\\.buckyos_installer_defaults\\...`
      and postinstall will copy to real paths only if missing
    """
    # modules -> real target
    for rel in layout.module_paths:
        rel_s = rel.strip()
        if rel_s.startswith("/") or rel_s.startswith("\\"):
            rel_s = rel_s[1:]
        rel_s = rel_s.rstrip("/\\")
        s = common.source_path_for(
            source_rootfs=layout.source_rootfs,
            rel=rel,
            item_source_paths=layout.module_source_paths,
            source_root_override=src_root,
            windows=True,
        )
        d = dst_root / rel_s
        if not s.exists():
            raise FileNotFoundError(
                f"module source missing: '{rel}' -> '{s}'. "
                f"Please ensure it exists under the publish root ({src_root}), "
                "or remove it from apps.buckyos.modules."
            )
        if s.is_dir():
            _copytree_filtered(s, d)
        else:
            if (
                s.suffix.lower() in (".ps1", ".cmd", ".exe")
                and not d.suffix
                and s.stem.lower() == d.name.lower()
            ):
                d = d.with_name(s.name)
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)

    # data_paths -> defaults area
    defaults_root = dst_root / BUCKYOS_DEFAULTS_SUBDIR
    for rel in layout.data_paths:
        rel_s = rel.strip()
        if rel_s.startswith("/") or rel_s.startswith("\\"):
            rel_s = rel_s[1:]
        rel_s = rel_s.rstrip("/\\")
        s = common.source_path_for(
            source_rootfs=layout.source_rootfs,
            rel=rel,
            item_source_paths=layout.data_source_paths,
            source_root_override=src_root,
            windows=True,
        )
        d = defaults_root / rel_s
        if not s.exists():
            raise FileNotFoundError(
                f"data_paths source missing: '{rel}' -> '{s}'. "
                f"Please ensure it exists under the publish root ({src_root}), "
                "or remove it from apps.buckyos.data_paths."
            )
        if s.is_dir():
            _copytree_filtered(s, d)
        else:
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)


def _run(cmd: List[str], dry_run: bool, cwd: Path | None = None) -> int:
    print("+", " ".join(cmd))
    if dry_run:
        return 0
    result = subprocess.run(cmd, cwd=cwd)
    return result.returncode


def _escape_nsis_path(s: str) -> str:
    """Escape path for NSIS."""
    return s.replace("\\", "\\\\")


def _escape_nsis_string(s: str) -> str:
    """Escape string for NSIS."""
    return s.replace('"', '$\\"').replace("\\", "\\\\")


class _NSISTemplates:
    """Named, literal NSIS fragments with single-pass @@NAME@@ substitution."""

    _section = re.compile(r"^; @@template ([A-Z][A-Z0-9_]*)@@\n", re.MULTILINE)
    _placeholder = re.compile(r"^@@([A-Z][A-Z0-9_]*)@@\n|@@([A-Z][A-Z0-9_]*)@@", re.MULTILINE)

    def __init__(self, path: Path) -> None:
        self.path = path
        self.sections: Dict[str, str] = {}
        parts = self._section.split(path.read_text(encoding="utf-8"))
        for name, body in zip(parts[1::2], parts[2::2]):
            if name in self.sections:
                raise ValueError(f"duplicate NSIS template section {name!r}: {path}")
            self.sections[name] = body
        if "INSTALLER" not in self.sections:
            raise ValueError(f"missing NSIS template section 'INSTALLER': {path}")

    def render(self, section: str, **values: str) -> str:
        if section not in self.sections:
            raise ValueError(f"missing NSIS template section {section!r}: {self.path}")

        def replace(match: re.Match[str]) -> str:
            name = match.group(1) or match.group(2)
            if name not in values:
                raise ValueError(f"missing NSIS placeholder {name!r} in {section!r}: {self.path}")
            return values[name]

        # Whole-line slots consume their newline. Inserted fragments supply their
        # own line endings, so an empty optional fragment leaves no blank line.
        # Do not rescan replacements: NSIS $, braces and user values are literal.
        return self._placeholder.sub(replace, self.sections[section])


def _render_windows_hook(
    templates: _NSISTemplates,
    comp: PublishComponent,
    comp_payload: Path,
    var_name: str,
    step: str,
) -> str:
    hook = _windows_payload_hook(comp_payload, step)
    if hook is None:
        return ""
    hook_rel = hook.relative_to(comp_payload).as_posix().replace("/", "\\")
    return templates.render(
        "HOOK", KEY=comp.key, STEP=step,
        LABEL=f"{_sanitize_id(comp.key).replace('-', '_')}_{step}",
        COMMAND=_hook_exec_command(var_name, hook_rel),
        ERROR_CODE={"preinstall": "50", "postinstall": "70", "preuninstall": "80"}[step],
    )


def generate_nsis_script(
    *,
    title: str,
    version: str,
    architecture: str,
    components: List[PublishComponent],
    payload_dir: Path,
    out_path: Path,
    license_file: Path | None = None,
    bundled_vcredist: Path | None = None,
) -> None:
    """Fill installer.nsi.in with component metadata and optional NSIS fragments."""
    templates = _NSISTemplates(WIN_PKG_PROJECT_DIR / "installer.nsi.in")
    render = templates.render
    is_x64 = architecture in ("amd64", "x86_64")
    has_vcredist = bundled_vcredist is not None and bundled_vcredist.exists()
    launch_bundle = next((comp for comp in components if comp.kind == "bundle"), None)
    has_buckyosapp_bundle = any(
        comp.kind == "bundle" and comp.key.casefold() == "buckyosapp"
        for comp in components
    )
    bundled_stop_script = next(
        (
            payload_dir / comp.key / "bin" / "stop.ps1"
            for comp in components
            if comp.system_service and (payload_dir / comp.key / "bin" / "stop.ps1").exists()
        ),
        None,
    )
    blocks: Dict[str, List[str]] = {key: [] for key in (
        "COMPONENT_VARIABLES", "DIRECTORY_VARIABLES", "DIRECTORY_CONTROLS",
        "BROWSE_CALLBACKS", "DIRECTORY_LEAVE", "COMPONENT_INIT",
        "COMPONENT_SECTIONS", "COMPONENT_DESCRIPTIONS", "UNINSTALL_SHORTCUTS",
        "UNINSTALL_COMPONENTS",
    )}
    for idx, comp in enumerate(components):
        label = _sanitize_id(comp.key).replace("-", "_")
        var = f"$InstDir_{label}"
        directory_var = f"$DirReq_{label}"
        section_id = f"SEC_{_sanitize_id(comp.key).upper()}"
        context = dict(KEY=comp.key, NAME=comp.name, INSTALL_VAR=var)
        comp_payload = payload_dir / comp.key
        blocks["COMPONENT_VARIABLES"].append(f"Var {var[1:]}\n")
        blocks["DIRECTORY_VARIABLES"].append(f"Var {directory_var[1:]}\n")
        blocks["DIRECTORY_CONTROLS"].append(render(
            "DIRECTORY_CONTROLS", **context, DIRECTORY_VAR=directory_var,
            INDEX=str(idx), LABEL_Y=str(idx * 40), INPUT_Y=str(idx * 40 + 14),
            BUTTON_Y=str(idx * 40 + 13),
        ))
        blocks["BROWSE_CALLBACKS"].append(render(
            "BROWSE_CALLBACKS", **context, DIRECTORY_VAR=directory_var, INDEX=str(idx),
        ))
        blocks["DIRECTORY_LEAVE"].append(f"  ${{NSD_GetText}} {directory_var} {var}\n")
        blocks["COMPONENT_INIT"].append(render(
            "COMPONENT_INIT", **context,
            SERVICE_ROOT_FALLBACK=render("SERVICE_ROOT_FALLBACK", **context) if comp.system_service else "",
            DEFAULT_TARGET=_escape_nsis_string(_nsis_default_target(comp)),
        ))

        payload_install = ""
        if comp_payload.exists():
            service_prerequisites = ""
            if comp.system_service:
                vc_section = "VCREDIST_CHECK_BUNDLED" if has_vcredist else "VCREDIST_CHECK_MISSING"
                service_prerequisites = render(
                    "SERVICE_PREREQUISITES", LABEL=label,
                    VCREDIST_CHECK=render(vc_section, LABEL=label),
                )
            stop_app = ""
            if comp.kind == "bundle" and comp.key.casefold() == "buckyosapp":
                stop_app = "  ; Stop the desktop app before overwriting a running executable\n  Call StopBuckyOSApp\n"
            payload_install = render(
                "PAYLOAD_INSTALL", **context, PAYLOAD_PATH=str(comp_payload),
                STOP_APP=stop_app, SERVICE_PREREQUISITES=service_prerequisites,
                PRE_HOOK=_render_windows_hook(templates, comp, comp_payload, var, "preinstall"),
            )
        bundle_install = ""
        if comp.kind == "bundle":
            exe_name = (comp.src or f"{comp.key}.exe").replace("/", "\\").split("\\")[-1]
            bundle_install = render("BUNDLE_INSTALL", **context, EXE_NAME=exe_name)
            blocks["UNINSTALL_SHORTCUTS"].append(
                f'  Delete "$SMPROGRAMS\\BuckyOS\\{comp.name}.lnk"\n'
                f'  Delete "$DESKTOP\\{comp.name}.lnk"\n'
            )
        blocks["COMPONENT_SECTIONS"].append(render(
            "COMPONENT_SECTIONS", **context, SECTION_ID=section_id,
            SECTION_FLAG=" /o" if comp.optional and not comp.default_selected else "",
            REQUIRED_SECTION="  SectionIn RO\n" if not comp.optional else "",
            PAYLOAD_INSTALL=payload_install, BUNDLE_INSTALL=bundle_install,
            SERVICE_INSTALL=render("SERVICE_INSTALL", **context) if comp.system_service else "",
            POST_HOOK=_render_windows_hook(templates, comp, comp_payload, var, "postinstall"),
        ))
        desc = f"Install {comp.name}" + (" (Optional)" if comp.optional else "")
        blocks["COMPONENT_DESCRIPTIONS"].append(
            f'  !insertmacro MUI_DESCRIPTION_TEXT ${{{section_id}}} "{desc}"\n'
        )
        blocks["UNINSTALL_COMPONENTS"].append(render(
            "UNINSTALL_COMPONENTS", **context,
            PREUNINSTALL_HOOK=_render_windows_hook(templates, comp, comp_payload, var, "preuninstall"),
            REMOVE_COMPONENT=(render("SERVICE_UNINSTALL", **context) if comp.system_service
                              else f'    RMDir /r "{var}"\n'),
        ))

    content = render(
        "INSTALLER", **{key: "".join(parts) for key, parts in blocks.items()},
        TITLE=title, VERSION=version, ARCHITECTURE=architecture,
        OUTPUT_FILENAME=common.package_filename(
            platform_key="windows", architecture=architecture, version=version, package_format="exe",
        ),
        ARCH_INCLUDE='!include "x64.nsh"\n' if is_x64 else "",
        DEFAULT_INSTALL_DIR=r"$PROGRAMFILES64\BuckyOS" if is_x64 else r"$PROGRAMFILES\BuckyOS",
        FINISH_PAGE_OPTIONS=render("FINISH_PAGE_OPTIONS", NAME=launch_bundle.name) if launch_bundle else "",
        LICENSE_PAGE=f'!insertmacro MUI_PAGE_LICENSE "{license_file}"\n' if license_file and license_file.exists() else "",
        VCREDIST_FUNCTIONS=(render("VCREDIST_FUNCTIONS", VCREDIST_PATH=_escape_nsis_path(str(bundled_vcredist)))
                           if has_vcredist else ""),
        BUNDLED_STOP_SCRIPT=(f'  File /oname=stop.ps1 "{_escape_nsis_path(str(bundled_stop_script))}"\n'
                             if bundled_stop_script is not None else ""),
        STOP_APP_FUNCTIONS=("".join(render("STOP_APP_FUNCTIONS", FUNCTION_NAME=name)
                                   for name in ("StopBuckyOSApp", "un.StopBuckyOSApp"))
                            if has_buckyosapp_bundle else ""),
        ARCH_INIT=render("ARCH_INIT") if is_x64 else "",
        LAUNCH_FUNCTION=render("LAUNCH_FUNCTION") if launch_bundle else "",
        FIRST_INSTALL_VAR=f"$InstDir_{_sanitize_id(components[0].key).replace('-', '_')}",
        UNINSTALL_REG_VIEW="  SetRegView 64\n" if is_x64 else "",
        UNINSTALL_STOP_APP=("  ; Stop the desktop app before removing its executable\n  Call un.StopBuckyOSApp\n"
                            if has_buckyosapp_bundle else ""),
    )
    # Explicit bytes avoid platform newline translation; NSIS consumes UTF-8 BOM + CRLF.
    out_path.write_bytes(content.replace("\n", "\r\n").encode("utf-8-sig"))


def _print_file_tree(path: Path, prefix: str = "", is_last: bool = True) -> None:
    """Print a file tree for dry-run output."""
    connector = "└── " if is_last else "├── "
    print(f"{prefix}{connector}{path.name}")
    
    if path.is_dir():
        children = [child for child in path.iterdir() if child.name not in IGNORED_STAGE_NAMES]
        children.sort(key=lambda x: (not x.is_dir(), x.name.lower()))
        for i, child in enumerate(children[:20]):  # Limit to 20 items
            extension = "    " if is_last else "│   "
            _print_file_tree(child, prefix + extension, i == len(children) - 1)
        if len(children) > 20:
            extension = "    " if is_last else "│   "
            print(f"{prefix}{extension}... and {len(children) - 20} more items")


def build_win_installer(
    *,
    architecture: str,
    version: str,
    project_yaml_path: Path,
    manifest_path: Path | None,
    app_publish_dir: Path,
    out_dir: Path,
    dry_run: bool = False,
) -> Path:
    """Build the Windows NSIS installer."""

    components = (
        load_win_pkg_components_from_manifest(manifest_path)
        if manifest_path is not None
        else load_win_pkg_components(project_yaml_path)
    )
    
    work_dir = TMP_INSTALL_DIR / "distbuild"
    payload_dir = work_dir / "payload"
    nsi_file = work_dir / "installer.nsi"
    
    # Keep scripts in sync with bucky_project.yaml before building.
    if not dry_run:
        sync_win_scripts(project_yaml_path, WIN_PKG_PROJECT_DIR / "scripts", manifest_path=manifest_path)
    
    if work_dir.exists() and not dry_run:
        shutil.rmtree(work_dir, ignore_errors=True)
    if not dry_run:
        work_dir.mkdir(parents=True, exist_ok=True)
        payload_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"[build] Staging components to {payload_dir}")
    
    for comp in components:
        src = _resolve_component_src(comp, app_publish_dir)
        
        # Try fallback paths
        if not src.exists():
            alt = app_publish_dir / "publish" / comp.key
            if alt.exists():
                src = alt
        
        if dry_run:
            print(f"\n[dry-run] Component: {comp.name} ({comp.key})")
            print(f"  Type: {comp.kind}")
            print(f"  Optional: {comp.optional}")
            print(f"  Default Selected: {comp.default_selected}")
            print(f"  System Service: {comp.system_service}")
            print(f"  Source: {src}")
            print(f"  Target: {comp.default_target}")
            if src.exists():
                print(f"  Source exists: YES")
                if src.is_dir():
                    print("  Files:")
                    _print_file_tree(src, "    ")
            else:
                print(f"  Source exists: NO (will fail during actual build)")
            continue
        
        if not src.exists():
            raise FileNotFoundError(f"component source not found: {comp.key} -> {src}")
        
        comp_payload = payload_dir / comp.key
        comp_payload.mkdir(parents=True, exist_ok=True)
        _stage_windows_hooks(comp.key, comp_payload)
        
        if comp.kind == "app":
            layout = resolve_app_layout(
                app_key=comp.key,
                project_yaml_path=project_yaml_path,
                manifest_path=manifest_path,
                target_override=comp.default_target,
            )
            _stage_buckyos_app_root(src_root=src, dst_root=comp_payload, layout=layout)
            
            if comp.system_service:
                scripts_src = WIN_PKG_PROJECT_DIR / "scripts"
                scripts_dst = comp_payload / "scripts"
                if scripts_src.exists():
                    _copy_windows_service_scripts(scripts_src, scripts_dst)
        else:
            if src.is_dir():
                _copy_dir_contents(src, comp_payload)
            else:
                shutil.copy2(src, comp_payload / src.name)

    if dry_run:
        print(f"\n[dry-run] Would generate NSIS script: {nsi_file}")
        out_name = common.package_filename(
            platform_key="windows",
            architecture=architecture,
            version=version,
            package_format="exe",
        )
        print(f"[dry-run] Would compile installer to: {out_dir / out_name}")
        return out_dir / out_name
    
    # Generate NSIS script
    license_file = WIN_PKG_PROJECT_DIR / "license.txt"
    staged_license_file: Path | None = None
    if license_file.exists():
        staged_license_file = _write_nsis_license_rtf(
            license_file,
            work_dir / "license.rtf",
        )
    generate_nsis_script(
        title="BuckyOS",
        version=version,
        architecture=architecture,
        components=components,
        payload_dir=payload_dir,
        out_path=nsi_file,
        license_file=staged_license_file,
        bundled_vcredist=(WIN_PKG_PROJECT_DIR / "vcredist_x64.exe"),
    )
    print(f"[build] Generated NSIS script: {nsi_file}")
    
    # Compile with NSIS
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # Try to find makensis
    makensis_paths = [
        "makensis",
        "C:\\Program Files (x86)\\NSIS\\makensis.exe",
        "C:\\Program Files\\NSIS\\makensis.exe",
    ]
    
    makensis_cmd = None
    for path in makensis_paths:
        try:
            result = subprocess.run([path, "/VERSION"], capture_output=True)
            if result.returncode == 0:
                makensis_cmd = path
                break
        except FileNotFoundError:
            continue
    
    if not makensis_cmd:
        raise RuntimeError(
            "makensis not found. Please install NSIS from https://nsis.sourceforge.io/ "
            "and ensure it's in your PATH."
        )
    
    cmd = [makensis_cmd, "/V3", str(nsi_file)]
    print(f"[build] Compiling installer with NSIS...")
    rc = _run(cmd, dry_run=False, cwd=work_dir)
    
    if rc != 0:
        raise RuntimeError(f"NSIS compilation failed with code {rc}")
    
    # Move output to target directory
    out_name = common.package_filename(
        platform_key="windows",
        architecture=architecture,
        version=version,
        package_format="exe",
    )
    built_exe = work_dir / out_name
    out_exe = out_dir / out_name
    
    if built_exe.exists():
        shutil.move(str(built_exe), str(out_exe))
        print(f"[build] Installer created: {out_exe}")
        return out_exe
    else:
        raise RuntimeError(f"Expected output not found: {built_exe}")


def verify_pkg(
    *,
    pkg_path: Path,
    project_yaml_path: Path,
    manifest_path: Path | None = None,
) -> int:
    """
    Verify a built Windows installer using 7zip.

    Checks:
    - File exists and is valid archive
    - Expected components are present
    - Metadata matches YAML configuration
    """
    if not pkg_path.exists():
        print(f"VERIFY FAIL: Installer not found: {pkg_path}")
        return 1
    
    components = (
        load_win_pkg_components_from_manifest(manifest_path)
        if manifest_path is not None
        else load_win_pkg_components(project_yaml_path)
    )
    failures: List[str] = []
    
    # Try to find 7z
    sz_paths = [
        "7z",
        "C:\\Program Files\\7-Zip\\7z.exe",
        "C:\\Program Files (x86)\\7-Zip\\7z.exe",
    ]
    
    sz_cmd = None
    for path in sz_paths:
        try:
            result = subprocess.run([path], capture_output=True)
            sz_cmd = path
            break
        except FileNotFoundError:
            continue
    
    if not sz_cmd:
        print("[verify] Warning: 7-Zip not found, skipping archive inspection")
        print("[verify] Install 7-Zip from https://www.7-zip.org/ for full verification")
    else:
        with tempfile.TemporaryDirectory(prefix="buckyos-verify-") as td:
            work = Path(td)
            extract_dir = work / "extracted"
            
            # Extract installer
            cmd = [sz_cmd, "x", f"-o{extract_dir}", str(pkg_path), "-y"]
            print(f"[verify] Extracting installer...")
            result = subprocess.run(cmd, capture_output=True)
            
            if result.returncode != 0:
                failures.append(f"Failed to extract installer: {result.stderr.decode()}")
            else:
                extracted_basenames = {
                    path.name.lower()
                    for path in extract_dir.rglob("*")
                    if path.is_file()
                }

                # NSIS extraction does not preserve our logical component directories,
                # so verify by payload marker files instead of folder names.
                for comp in components:
                    expected_names = _component_expected_filenames(comp=comp, manifest_path=manifest_path)
                    if expected_names and not any(name in extracted_basenames for name in expected_names):
                        failures.append(
                            f"Component payload not detected: {comp.key} "
                            f"(expected files like {', '.join(expected_names[:5])})"
                        )

                for script in (
                    "seed_defaults.ps1",
                    "ensure_firewall_rules.ps1",
                    "uninstall_cleanup.ps1",
                    "node_daemon_loader.vbs",
                ):
                    if script.lower() not in extracted_basenames:
                        failures.append(f"Missing required script: {script}")
    
    # Verify file size is reasonable
    file_size = pkg_path.stat().st_size
    if file_size < 1024 * 1024:  # Less than 1MB is suspicious
        failures.append(f"Installer size suspiciously small: {file_size} bytes")
    
    print(f"[verify] Installer size: {file_size / (1024*1024):.2f} MB")
    
    if failures:
        print("VERIFY FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    
    print("VERIFY PASS")
    return 0


def _ps_rm_lines(root_var: str, rel_paths: List[str]) -> List[str]:
    """Generate PowerShell Remove-Item lines."""
    out: List[str] = []
    for rel in rel_paths:
        rel_s = rel.strip().lstrip("/\\").rstrip("/\\")
        if not rel_s:
            continue
        # Normalize path separators for Windows
        rel_s = rel_s.replace("/", "\\")
        out.append(f'Remove-Item -Recurse -Force -ErrorAction SilentlyContinue -Path (Join-Path {root_var} "{rel_s}")')
    return out


def _ps_data_copy_lines(root_var: str, defaults_var: str, rel_paths: List[str]) -> List[str]:
    """Generate PowerShell data copy lines for seed_defaults.ps1."""
    out: List[str] = []
    for rel in rel_paths:
        rel_s = rel.strip().lstrip("/\\")
        if not rel_s:
            continue
        # Normalize path separators
        rel_s = rel_s.replace("/", "\\")
        
        if rel_s.endswith("/") or rel_s.endswith("\\"):
            rel_s = rel_s.rstrip("/\\")
            # Directory copy
            out += [
                f'$src = Join-Path {defaults_var} "{rel_s}"',
                f'$dst = Join-Path {root_var} "{rel_s}"',
                "$shouldCopy = $false",
                'if (-not (Test-Path $dst)) { $shouldCopy = $true }',
                'elseif (-not (Get-ChildItem -LiteralPath $dst -Force -ErrorAction SilentlyContinue | Select-Object -First 1)) { $shouldCopy = $true }',
                'if ($shouldCopy -and (Test-Path $src)) {',
                '  New-Item -ItemType Directory -Force -Path $dst | Out-Null',
                '  Copy-Item -Recurse -Force -Path (Join-Path $src "*") -Destination $dst',
                '}',
            ]
        else:
            # File copy
            out += [
                f'$src = Join-Path {defaults_var} "{rel_s}"',
                f'$dst = Join-Path {root_var} "{rel_s}"',
                'if (-not (Test-Path $dst)) {',
                '  if (Test-Path $src) {',
                '    New-Item -ItemType Directory -Force -Path (Split-Path $dst -Parent) | Out-Null',
                '    Copy-Item -Force -Path $src -Destination $dst',
                '  }',
                '}',
            ]
    return out


def _render_seed_defaults_script(data_paths: List[str]) -> str:
    """Render the full seed_defaults.ps1 script content."""
    lines: List[str] = [
        '$ErrorActionPreference = "Stop"',
        "",
        "# AUTO-GENERATED by make_local_win_installer.py (sync_win_scripts)",
        "# Do not edit this file manually.",
        "",
        "$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path",
        "$Root = Split-Path -Parent $ScriptDir",
        '$DefaultsDir = Join-Path $Root ".buckyos_installer_defaults"',
        "",
    ]
    lines.extend(_ps_data_copy_lines("$Root", "$DefaultsDir", data_paths))
    return "\n".join(lines).rstrip("\n") + "\n"


def _render_firewall_setup_script() -> str:
    """Render the Windows Firewall setup script for installed service executables."""
    lines: List[str] = [
        '$ErrorActionPreference = "Continue"',
        "",
        "# AUTO-GENERATED by make_local_win_installer.py (sync_win_scripts)",
        "# Do not edit this file manually.",
        "",
        "$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path",
        "$Root = Split-Path -Parent $ScriptDir",
        '$BinDir = Join-Path $Root "bin"',
        '$RulePrefix = "BuckyOS Service"',
        "",
        "function Get-RuleName {",
        "  param([string]$ProgramPath)",
        "  $relative = $ProgramPath.Substring($Root.Length).TrimStart('\\\\')",
        '  return "$RulePrefix - $relative"',
        "}",
        "",
        "if (-not (Test-Path $BinDir)) {",
        "  exit 0",
        "}",
        "",
        '$hasNetSecurity = $null -ne (Get-Command New-NetFirewallRule -ErrorAction SilentlyContinue)',
        "$programs = Get-ChildItem -Path $BinDir -Recurse -File -Filter *.exe -ErrorAction SilentlyContinue | Sort-Object FullName",
        "foreach ($program in $programs) {",
        "  $programPath = $program.FullName",
        "  $ruleName = Get-RuleName -ProgramPath $programPath",
        "  if ($hasNetSecurity) {",
        "    Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue | Out-Null",
        '    New-NetFirewallRule -DisplayName $ruleName -Group "BuckyOS" -Direction Inbound -Action Allow -Program $programPath -Profile Any | Out-Null',
        "    continue",
        "  }",
        '  & netsh advfirewall firewall delete rule name="$ruleName" program="$programPath" > $null 2>&1',
        '  & netsh advfirewall firewall add rule name="$ruleName" dir=in action=allow program="$programPath" enable=yes profile=any > $null',
        "}",
        "",
    ]
    return "\n".join(lines).rstrip("\n") + "\n"


def _render_uninstall_cleanup_script(module_paths: List[str], clean_paths: List[str]) -> str:
    """Render the full uninstall_cleanup.ps1 script content."""
    lines: List[str] = [
        '$ErrorActionPreference = "Continue"',
        "",
        "# AUTO-GENERATED by make_local_win_installer.py (sync_win_scripts)",
        "# Do not edit this file manually.",
        "",
        "$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path",
        "$Root = Split-Path -Parent $ScriptDir",
        '$DefaultsDir = Join-Path $Root ".buckyos_installer_defaults"',
        '$BinDir = Join-Path $Root "bin"',
        '$RulePrefix = "BuckyOS Service"',
        "",
        "Remove-Item -Recurse -Force -ErrorAction SilentlyContinue -Path $DefaultsDir",
        "",
        "function Get-RuleName {",
        "  param([string]$ProgramPath)",
        "  $relative = $ProgramPath.Substring($Root.Length).TrimStart('\\\\')",
        '  return "$RulePrefix - $relative"',
        "}",
        "",
        "if (Test-Path $BinDir) {",
        '  $hasNetSecurity = $null -ne (Get-Command Get-NetFirewallRule -ErrorAction SilentlyContinue)',
        "  $programs = Get-ChildItem -Path $BinDir -Recurse -File -Filter *.exe -ErrorAction SilentlyContinue | Sort-Object FullName",
        "  foreach ($program in $programs) {",
        "    $programPath = $program.FullName",
        "    $ruleName = Get-RuleName -ProgramPath $programPath",
        "    if ($hasNetSecurity) {",
        "      Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue | Out-Null",
        "      continue",
        "    }",
        '    & netsh advfirewall firewall delete rule name="$ruleName" program="$programPath" > $null 2>&1',
        "  }",
        "}",
    ]
    clean_lines = _ps_rm_lines("$Root", clean_paths)
    module_lines = _ps_rm_lines("$Root", module_paths)
    if clean_lines:
        lines.extend(["", *clean_lines])
    if module_lines:
        lines.extend(["", *module_lines])
    return "\n".join(lines).rstrip("\n") + "\n"


def sync_win_scripts(
    project_yaml_path: Path,
    scripts_dir: Path,
    *,
    manifest_path: Path | None = None,
) -> None:
    """
    Update PowerShell scripts based on bucky_project.yaml.

    - seed_defaults.ps1: full file is auto-generated.
    - ensure_firewall_rules.ps1: full file is auto-generated.
    - uninstall_cleanup.ps1: full file is auto-generated.
    
    Note: Currently only syncs buckyos app scripts, as the PowerShell
    scripts are specifically designed for the buckyos component.
    """
    # Ensure scripts directory exists
    scripts_dir.mkdir(parents=True, exist_ok=True)
    
    # Only process buckyos app layout for the main scripts
    layout = resolve_app_layout(
        app_key="buckyos",
        project_yaml_path=project_yaml_path,
        manifest_path=manifest_path,
    )
    
    # Re-generate full seed_defaults.ps1
    seed_script = scripts_dir / "seed_defaults.ps1"
    seed_script.write_text(
        _render_seed_defaults_script(layout.data_paths),
        encoding="utf-8-sig",
    )
    print(f"[sync] Updated: {seed_script}")

    firewall_script = scripts_dir / "ensure_firewall_rules.ps1"
    firewall_script.write_text(
        _render_firewall_setup_script(),
        encoding="utf-8-sig",
    )
    print(f"[sync] Updated: {firewall_script}")
    
    # Re-generate full uninstall_cleanup.ps1
    uninstall_script = scripts_dir / "uninstall_cleanup.ps1"
    uninstall_script.write_text(
        _render_uninstall_cleanup_script(layout.module_paths, layout.clean_paths),
        encoding="utf-8-sig",
    )
    print(f"[sync] Updated: {uninstall_script}")


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="make_local_win_installer.py",
        description="Build Windows NSIS installer for BuckyOS"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    
    # build-pkg command
    p_build = sub.add_parser("build-pkg", help="Build Windows .exe installer using NSIS")
    p_build.add_argument("architecture", help="amd64|arm64")
    p_build.add_argument("version", help="Version string (e.g., 0.5.1+build260114)")
    p_build.add_argument("--project", default=str(PROJECT_YAML), help="Path to bucky_project.yaml")
    p_build.add_argument("--manifest", default=None, help="Path to generated project manifest JSON")
    p_build.add_argument(
        "--app-publish-dir",
        default=str(RESULT_ROOT_DIR),
        help="Base directory to resolve publish.win_pkg.apps sources (default: $BUCKYOS_BUILD_ROOT)"
    )
    p_build.add_argument(
        "--out-dir",
        default=str(Path.cwd() / "publish"),
        help='Output directory for the final .exe (default: "./publish")'
    )
    p_build.add_argument("--dry-run", action="store_true", help="Preview build without executing NSIS")
    
    # verify command
    p_verify = sub.add_parser("verify-pkg", aliases=["verify"], help="Verify a built installer using 7zip")
    p_verify.add_argument("--pkg", required=True, help="Path to .exe installer")
    p_verify.add_argument("--project", default=str(PROJECT_YAML), help="Path to bucky_project.yaml")
    p_verify.add_argument("--manifest", default=None, help="Path to generated project manifest JSON")

    # sync command
    p_sync = sub.add_parser("sync", help="Regenerate PowerShell scripts from bucky_project.yaml")
    p_sync.add_argument("--project", default=str(PROJECT_YAML), help="Path to bucky_project.yaml")
    p_sync.add_argument("--manifest", default=None, help="Path to generated project manifest JSON")
    
    args = parser.parse_args(argv[1:])
    
    if args.cmd == "build-pkg":
        arch = args.architecture
        if arch == "x86_64":
            arch = "amd64"
        
        out_exe = build_win_installer(
            architecture=arch,
            version=args.version,
            project_yaml_path=Path(args.project),
            manifest_path=Path(args.manifest).resolve() if args.manifest else None,
            app_publish_dir=Path(args.app_publish_dir),
            out_dir=Path(args.out_dir),
            dry_run=bool(args.dry_run),
        )
        print(f"Installer built: {out_exe}")
        return 0
    
    if args.cmd in ("verify", "verify-pkg"):
        return verify_pkg(
            pkg_path=Path(args.pkg).expanduser().resolve(),
            project_yaml_path=Path(args.project),
            manifest_path=Path(args.manifest).resolve() if args.manifest else None,
        )

    if args.cmd == "sync":
        sync_win_scripts(
            Path(args.project),
            WIN_PKG_PROJECT_DIR / "scripts",
            manifest_path=Path(args.manifest).resolve() if args.manifest else None,
        )
        print("win_pkg scripts synced.")
        return 0
    
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
