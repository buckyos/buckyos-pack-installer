from __future__ import annotations

import plistlib
import shutil
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import make_local_osx_pkg


class MakeLocalOsxPkgTests(unittest.TestCase):
    def test_pkg_target_uses_publish_config(self) -> None:
        component = make_local_osx_pkg.PublishComponent(
            key="command-line-tool",
            name="Command Line Tool",
            kind="app",
            optional=True,
            default_selected=True,
            src=None,
            default_target="/usr/local/bin/",
        )

        self.assertEqual(make_local_osx_pkg._resolve_component_pkg_target(component), Path("/usr/local/bin"))

    def test_project_scripts_follow_the_runtime_contract(self) -> None:
        scripts = make_local_osx_pkg.MACOS_PKG_PROJECT_DIR / "scripts"
        postinstall = (scripts / "buckyos_postinstall").read_text(encoding="utf-8")
        preinstall = (scripts / "buckyos_preinstall").read_text(encoding="utf-8")
        self.assertEqual(make_local_osx_pkg._verify_buckyos_postinstall(postinstall), [])
        self.assertEqual(make_local_osx_pkg._verify_buckyos_preinstall(preinstall), [])

    def test_script_checks_reject_the_system_service_setup(self) -> None:
        legacy = (
            'PLIST="/Library/LaunchDaemons/buckyos.service.plist"\n'
            '# END AUTO-GENERATED: data_paths\n'
            'launchctl bootstrap system "$PLIST"\n'
        )
        failures = make_local_osx_pkg._verify_buckyos_postinstall(legacy)
        self.assertTrue(any("/Library/LaunchDaemons" in failure for failure in failures))
        self.assertTrue(any("service register" in failure for failure in failures))
        blocking = 'check_user_docker() {\n  docker info || exit 1\n}\n'
        self.assertTrue(any(
            "must not block" in failure for failure in make_local_osx_pkg._verify_buckyos_preinstall(blocking)
        ))

    def test_runtime_payload_has_exactly_one_node_daemon(self) -> None:
        bundle = make_local_osx_pkg.RUNTIME_PAYLOAD_BUNDLE
        files = [
            f"{bundle}/Contents/MacOS/node_daemon",
            f"{bundle}/Contents/Info.plist",
            f"{bundle}/Contents/_CodeSignature/CodeResources",
            "opt/buckyos/.buckyos_installer_defaults/local/node_daemon",
        ]
        self.assertEqual(make_local_osx_pkg._verify_runtime_payload(files), ([], []))
        failures, copies = make_local_osx_pkg._verify_runtime_payload(
            files[1:] + ["opt/buckyos/bin/node-daemon/node_daemon"]
        )
        self.assertEqual(copies, ["opt/buckyos/bin/node-daemon/node_daemon"])
        self.assertEqual(len(failures), 2)

    @unittest.skipUnless(shutil.which("pkgbuild"), "needs macOS pkgbuild")
    def test_component_plist_pins_bundles_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            contents = root / "opt" / "buckyos" / "bin" / "node-daemon" / "BuckyOS Runtime.app" / "Contents"
            (contents / "MacOS").mkdir(parents=True)
            shutil.copyfile(make_local_osx_pkg.MACOS_PKG_PROJECT_DIR.parent.parent / "kernel" / "node_daemon" / "macos" / "Info.plist", contents / "Info.plist")
            (contents / "MacOS" / "node_daemon").write_bytes(b"#!/bin/sh\n")
            out = Path(temporary) / "component.plist"
            self.assertTrue(make_local_osx_pkg._write_component_plist(root, out, dry_run=False))
            with out.open("rb") as handle:
                bundles = plistlib.load(handle)
            self.assertTrue(bundles)
            self.assertTrue(all(bundle["BundleIsRelocatable"] is False for bundle in bundles))
            empty = Path(temporary) / "empty"
            (empty / "opt").mkdir(parents=True)
            self.assertFalse(make_local_osx_pkg._write_component_plist(empty, Path(temporary) / "none.plist", dry_run=False))


if __name__ == "__main__":
    unittest.main()
