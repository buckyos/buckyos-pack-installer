from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import make_local_pkg


def _write_project(
    path: Path,
    *,
    app_key: str,
    modules: dict[str, str],
    deps: dict[str, dict[str, str]] | None = None,
) -> None:
    data: dict = {
        "name": app_key,
        "version": "0.1.0",
        "base_dir": ".",
        "apps": {
            app_key: {
                "name": app_key,
                "rootfs": "rootfs/",
                "default_target_rootfs": "${BUCKYOS_ROOT}",
                "modules": modules,
                "data_paths": [],
                "clean_paths": [],
            }
        },
    }
    if app_key == "buckyos":
        component: dict = {
            "name": "BuckyOS Service",
            "type": "app",
            "default_target": "/opt/buckyos/",
        }
        if deps is not None:
            component["deps"] = deps
        data["publish"] = {
            "linux_pkg": {
                "apps": {
                    "buckyos": component,
                }
            }
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


class MakeLocalPkgManifestTests(unittest.TestCase):
    def test_buckyos_project_dep_is_merged_into_package_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            buckyos_project = root / "buckyos" / "src" / "bucky_project.json"
            cyfs_project = root / "cyfs-gateway" / "src" / "bucky_project.json"
            _write_project(
                buckyos_project,
                app_key="buckyos",
                modules={"node_daemon": "bin/node-daemon/"},
                deps={
                    "cyfs-gateway": {
                        "type": "buckyos_project",
                        "source": "../../cyfs-gateway/src",
                    }
                },
            )
            _write_project(
                cyfs_project,
                app_key="cyfs-gateway",
                modules={
                    "cyfs-gateway": "bin/cyfs-gateway/",
                    "test-server": "bin/test-server/",
                },
            )
            (root / "stage" / "buckyos" / "bin" / "cyfs-gateway").mkdir(parents=True)
            (root / "stage" / "buckyos" / "bin" / "test-server").mkdir(parents=True)

            manifest = make_local_pkg._build_project_manifest(
                buckyos_project,
                build_root=root / "stage",
                app_publish_dir=root / "stage",
            )

        buckyos = manifest["install_projects"]["buckyos"]
        module_paths = {item["raw_path"] for item in buckyos["module_items"]}
        self.assertIn("bin/node-daemon/", module_paths)
        self.assertIn("bin/cyfs-gateway/", module_paths)
        self.assertIn("bin/test-server/", module_paths)
        dep_item = next(item for item in buckyos["module_items"] if item["raw_path"] == "bin/cyfs-gateway/")
        self.assertEqual(dep_item["source_project_path"], str(cyfs_project.resolve()))
        self.assertEqual(dep_item["source_path"], str((root / "stage" / "buckyos" / "bin" / "cyfs-gateway").resolve()))
        self.assertEqual(buckyos["publish_deps"][0]["project_path"], str(cyfs_project.resolve()))

    def test_dep_source_is_resolved_relative_to_project_config_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            buckyos_project = root / "repo" / "src" / "bucky_project.json"
            cyfs_project = root / "cyfs-gateway" / "src" / "bucky_project.json"
            _write_project(
                buckyos_project,
                app_key="buckyos",
                modules={},
                deps={
                    "cyfs-gateway": {
                        "type": "buckyos_project",
                        "source": "../../cyfs-gateway/src",
                    }
                },
            )
            _write_project(cyfs_project, app_key="cyfs-gateway", modules={"cyfs-gateway": "bin/cyfs-gateway/"})

            dep = make_local_pkg._publish_dependencies_from_component(
                project_file=buckyos_project.resolve(),
                platform_key="linux",
                component_key="buckyos",
                component_cfg={
                    "deps": {
                        "cyfs-gateway": {
                            "type": "buckyos_project",
                            "source": "../../cyfs-gateway/src",
                        }
                    }
                },
            )[0]

        self.assertEqual(dep.project_file, cyfs_project.resolve())
        self.assertEqual(dep.source_dir, cyfs_project.parent.resolve())

    def test_missing_dep_source_fails_even_without_prepare(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            buckyos_project = root / "buckyos" / "src" / "bucky_project.json"
            _write_project(
                buckyos_project,
                app_key="buckyos",
                modules={},
                deps={
                    "cyfs-gateway": {
                        "type": "buckyos_project",
                        "source": "../../cyfs-gateway/src",
                    }
                },
            )

            with self.assertRaisesRegex(RuntimeError, "cyfs-gateway"):
                make_local_pkg._build_project_manifest(
                    buckyos_project,
                    build_root=root / "stage",
                    app_publish_dir=root / "stage",
                )

    def test_prepare_publish_dependency_runs_external_project_install(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            buckyos_project = root / "buckyos" / "src" / "bucky_project.json"
            cyfs_project = root / "cyfs-gateway" / "src" / "bucky_project.json"
            _write_project(
                buckyos_project,
                app_key="buckyos",
                modules={},
                deps={
                    "cyfs-gateway": {
                        "type": "buckyos_project",
                        "source": "../../cyfs-gateway/src",
                    }
                },
            )
            _write_project(cyfs_project, app_key="cyfs-gateway", modules={"cyfs-gateway": "bin/cyfs-gateway/"})
            target = make_local_pkg.TargetScript(
                platform_key="linux",
                package_format="deb",
                script_path=Path("make_local_deb.py"),
                architecture="amd64",
                build_root=root / "stage",
            )
            calls: list[tuple[list[str], Path | None, bool]] = []
            original_run_checked = make_local_pkg._run_checked

            def fake_run_checked(cmd: list[str], *, cwd: Path | None = None, dry_run: bool = False) -> None:
                calls.append((cmd, cwd, dry_run))

            try:
                make_local_pkg._run_checked = fake_run_checked
                make_local_pkg._prepare_publish_dependencies(
                    project_path=buckyos_project,
                    target=target,
                    dry_run=True,
                    skip_cargo_update=False,
                    rust_target="x86_64-unknown-linux-gnu",
                )
            finally:
                make_local_pkg._run_checked = original_run_checked

        self.assertEqual(calls[0], (["cargo", "update"], cyfs_project.parent.resolve(), True))
        self.assertEqual(calls[1], (["buckyos-build", "--target=x86_64-unknown-linux-gnu"], cyfs_project.parent.resolve(), True))
        self.assertEqual(
            calls[2],
            (
                ["buckyos-install", "--all", f"--target-rootfs={root / 'stage' / 'buckyos'}", "--app=cyfs-gateway"],
                cyfs_project.parent.resolve(),
                True,
            ),
        )


if __name__ == "__main__":
    unittest.main()
