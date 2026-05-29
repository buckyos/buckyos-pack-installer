from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import make_local_rpm as rpm


class MakeLocalRpmTests(unittest.TestCase):
    def test_rpm_arch_and_version_release(self) -> None:
        self.assertEqual(rpm._rpm_arch("amd64"), "x86_64")
        self.assertEqual(rpm._rpm_arch("arm64"), "aarch64")
        self.assertEqual(rpm._rpm_version_release("0.6.0+build260529"), ("0.6.0", "build260529"))
        self.assertEqual(rpm._rpm_version_release("0.6.0-dev+build-1"), ("0.6.0_dev", "build_1"))

    def test_render_spec_includes_linux_package_contract(self) -> None:
        layout = rpm.AppLayout(
            source_rootfs=Path("/stage/buckyos"),
            target_rootfs=Path("/opt/buckyos"),
            module_paths=["bin/node-daemon/"],
            data_paths=["etc/node_gateway.json", "data/"],
            clean_paths=[],
            module_source_paths={},
            data_source_paths={},
        )
        spec = rpm._render_spec(
            rpm_version="0.6.0",
            rpm_release="build260529",
            rpm_architecture="aarch64",
            payload_tree=Path("/tmp/buckyos-payload"),
            layout=layout,
            component_keys=["buckyos"],
        )

        self.assertIn("Version: 0.6.0", spec)
        self.assertIn("Release: build260529", spec)
        self.assertIn("BuildArch: aarch64", spec)
        self.assertIn("Requires: (docker-ce or moby-engine or docker-engine)", spec)
        self.assertIn('rm -rf "$BUCKYOS_ROOT/bin/"', spec)
        self.assertIn(".buckyos_installer_defaults", spec)
        self.assertIn("%preun", spec)
        self.assertIn("/etc/systemd/system/buckyos.service", spec)
        self.assertIn('cp -a /tmp/buckyos-payload/. "%{buildroot}/"', spec)


if __name__ == "__main__":
    unittest.main()
