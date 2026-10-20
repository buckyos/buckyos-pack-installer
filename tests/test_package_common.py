from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import package_common as common


class PackageCommonTests(unittest.TestCase):
    def test_project_load_file_accepts_json(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project = Path(td) / "bucky_project.json"
            project.write_text('{"name":"buckyos"}', encoding="utf-8")

            self.assertEqual(common.project_load_file(project), {"name": "buckyos"})

    def test_parse_bool_rejects_ambiguous_strings(self) -> None:
        self.assertTrue(common.parse_bool("true", field_name="field"))
        self.assertFalse(common.parse_bool("false", field_name="field"))
        with self.assertRaises(ValueError):
            common.parse_bool("true,", field_name="field")

    def test_parse_component_type(self) -> None:
        self.assertEqual(common.parse_component_type("app", field_name="type"), "app")
        self.assertEqual(common.parse_component_type("bundle", field_name="type"), "bundle")
        with self.assertRaises(ValueError):
            common.parse_component_type("service", field_name="type")

    def test_arch_and_package_filename(self) -> None:
        self.assertEqual(common.canonical_arch("x86_64"), "amd64")
        self.assertEqual(common.canonical_arch("aarch64"), "arm64")
        self.assertEqual(
            common.package_filename(
                platform_key="macos",
                architecture="aarch64",
                version="0.6.0+build260528",
                package_format="pkg",
            ),
            "buckyos-macos-arm64-0.6.0+build260528.pkg",
        )

    def test_manifest_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            manifest = Path(td) / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "platforms": {"linux": {"component_keys": ["buckyos"]}},
                        "install_projects": {
                            "buckyos": {
                                "module_items": [
                                    {
                                        "raw_path": "bin/node-daemon/",
                                        "source_path": "/stage/buckyos/bin/node-daemon",
                                    }
                                ],
                                "data_items": [],
                                "clean_items": [],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            project = common.manifest_install_project(manifest, "buckyos")
            self.assertEqual(common.manifest_component_keys(manifest, "linux"), ["buckyos"])
            self.assertEqual(
                common.item_paths(project, "module_items", project_key="buckyos"),
                ["bin/node-daemon/"],
            )
            self.assertEqual(
                common.item_source_paths(project, "module_items", project_key="buckyos"),
                {"bin/node-daemon/": "/stage/buckyos/bin/node-daemon"},
            )

    def test_discover_component_hook(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            scripts = Path(td)
            hook = scripts / "buckyos_preinstall.ps1"
            hook.write_text("exit 0\n", encoding="utf-8")
            self.assertEqual(
                common.discover_component_hook(
                    scripts_dirs=(scripts,),
                    component_key="buckyos",
                    step="preinstall",
                    extensions=(".ps1", ".cmd"),
                ),
                hook,
            )

    def test_source_path_for_prefers_manifest_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            mapped = Path(td) / "merged" / "bin" / "cyfs-gateway"
            mapped.mkdir(parents=True)
            self.assertEqual(
                common.source_path_for(
                    source_rootfs=root,
                    rel="bin/cyfs-gateway/",
                    item_source_paths={"bin/cyfs-gateway/": str(mapped)},
                ),
                mapped.resolve(),
            )

    def test_source_path_for_windows_launcher_fallback_order(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            source_root_override = Path(td) / "stage" / "tool"
            ps1 = source_root_override / "tool.ps1"
            cmd = source_root_override / "tool.cmd"
            exe = source_root_override / "tool.exe"
            exe.parent.mkdir(parents=True)
            ps1.write_bytes(b"ps1")
            cmd.write_bytes(b"cmd")
            exe.write_bytes(b"exe")

            self.assertEqual(
                common.source_path_for(
                    source_rootfs=root,
                    rel="tool",
                    item_source_paths={"tool": str(source_root_override / "tool")},
                    source_root_override=source_root_override,
                    windows=True,
                ),
                ps1,
            )

    def test_source_path_for_does_not_add_exe_on_non_windows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            exe = root / "tool.exe"
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"exe")

            self.assertEqual(
                common.source_path_for(
                    source_rootfs=root,
                    rel="tool",
                    item_source_paths={},
                    windows=False,
                ),
                root / "tool",
            )

    def test_unexpected_payload_paths(self) -> None:
        unexpected = common.unexpected_payload_paths(
            [
                "./opt",
                "./opt/buckyos",
                "./opt/buckyos/bin/node-daemon/node_daemon",
                "./opt/buckyos/.buckyos_installer_defaults/etc/node_gateway.json",
                "./opt/buckyos/data/user.db",
                "./Applications/BuckyOS.app/Contents/Info.plist",
            ],
            allowed_prefixes=[
                "opt/buckyos/bin/node-daemon",
                "opt/buckyos/.buckyos_installer_defaults/etc/node_gateway.json",
            ],
            allowed_exact=["opt", "opt/buckyos", "opt/buckyos/.buckyos_installer_defaults"],
        )
        self.assertEqual(
            unexpected,
            [
                "Applications/BuckyOS.app/Contents/Info.plist",
                "opt/buckyos/data/user.db",
            ],
        )

    def test_unexpected_data_payload_paths_allows_explicit_modules(self) -> None:
        unexpected = common.unexpected_data_payload_paths(
            [
                "./opt/buckyos/data",
                "./opt/buckyos/data/cache",
                "./opt/buckyos/data/cache/bootstrap.pikg",
                "./opt/buckyos/data/user.db",
                "./opt/buckyos/database/unrelated.db",
            ],
            target_root="/opt/buckyos",
            data_path="data/",
            module_paths=["data/cache/bootstrap.pikg"],
        )

        self.assertEqual(unexpected, ["opt/buckyos/data/user.db"])


    def test_verify_paios_images(self) -> None:
        import hashlib

        def manifest(entries: dict[str, tuple[str, bytes]]) -> dict:
            return {
                "schema": "buckyos.paios-images/v1",
                "images": {
                    name: {"platforms": {"aarch64": {"tar": {
                        "file": file_name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}}}}
                    for name, (file_name, data) in entries.items()
                },
            }

        entries = {"aios": ("aios-aarch64-1.tar.gz", b"aios"), "exttool": ("exttool-aarch64-2.tar.gz", b"ext")}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            failures: list[str] = []
            common.verify_paios_images(root, "arm64", failures, required=False)
            self.assertEqual(failures, [])
            common.verify_paios_images(root, "arm64", failures, required=True)
            self.assertEqual(len(failures), 1)

            images_dir = root / "opt/buckyos/bin/paios-images"
            images_dir.mkdir(parents=True)
            (images_dir / "paios_images.json").write_text(json.dumps(manifest(entries)), encoding="utf-8")
            failures = []
            common.verify_paios_images(root, "aarch64", failures, required=False)
            self.assertEqual(failures, [])
            common.verify_paios_images(root, "aarch64", failures, required=True)
            self.assertEqual(len(failures), 2)

            (images_dir / "aios-aarch64-1.tar.gz").write_bytes(b"aios")
            flattened = root / "flattened"
            flattened.mkdir()
            (flattened / "exttool-aarch64-2.tar.gz").write_bytes(b"ext")
            failures = []
            common.verify_paios_images(root, "arm64", failures, required=True)
            self.assertEqual(failures, [])

            (images_dir / "aios-aarch64-1.tar.gz").write_bytes(b"aiox")
            common.verify_paios_images(root, "arm64", failures, required=True)
            self.assertEqual(len(failures), 1)
            self.assertIn("does not match", failures[0])

            failures = []
            common.verify_paios_images(root, "amd64", failures, required=True)
            self.assertEqual(len(failures), 2)

    def test_package_arch_from_name(self) -> None:
        self.assertEqual(common.package_arch_from_name("buckyos-macos-arm64-0.7.0+build261020.pkg"), "arm64")
        self.assertEqual(common.package_arch_from_name("buckyos-windows-amd64-0.7.0+build261020.exe"), "amd64")
        with self.assertRaises(ValueError):
            common.package_arch_from_name("buckyos.pkg")

if __name__ == "__main__":
    unittest.main()
