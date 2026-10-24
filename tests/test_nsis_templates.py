import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import make_local_win_installer as winpkg  # noqa: E402


class NSISTemplateTests(unittest.TestCase):
    def test_resources_resolve_next_to_packager(self) -> None:
        resources = Path(winpkg.__file__).resolve().parent / "win_pkg"
        self.assertEqual(winpkg.WIN_PKG_PROJECT_DIR, resources)
        templates = winpkg._NSISTemplates(resources / "installer.nsi.in")
        self.assertIn("INSTALLER", templates.sections)
        self.assertTrue((resources / "license.txt").is_file())
        self.assertTrue((resources / "scripts/buckyos_postinstall.ps1").is_file())

    def test_substitution_preserves_nsis_syntax_and_does_not_rescan_values(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "installer.nsi.in"
            path.write_text(
                '; @@template INSTALLER@@\n'
                '${If} $0 != ""\n'
                '  DetailPrint "@@VALUE@@"\n'
                '@@OPTIONAL@@\n'
                '@@BODY@@\n'
                '${EndIf}\n',
                encoding="utf-8",
            )
            templates = winpkg._NSISTemplates(path)
            value = r'C:\路径 with spaces\$literal\@@BODY@@'
            result = templates.render(
                "INSTALLER", VALUE=value, OPTIONAL="", BODY="  Return\n",
            )
            self.assertEqual(
                result,
                '${If} $0 != ""\n'
                f'  DetailPrint "{value}"\n'
                '  Return\n'
                '${EndIf}\n',
            )

    def test_missing_placeholder_reports_section_and_name(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "installer.nsi.in"
            path.write_text('; @@template INSTALLER@@\n@@MISSING@@\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "MISSING.*INSTALLER"):
                winpkg._NSISTemplates(path).render("INSTALLER")

    def test_missing_and_duplicate_sections_fail_explicitly(self) -> None:
        for text, error in (
            ("; no sections\n", "missing.*INSTALLER"),
            ('; @@template INSTALLER@@\n; @@template INSTALLER@@\n', "duplicate.*INSTALLER"),
        ):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as td:
                path = Path(td) / "installer.nsi.in"
                path.write_text(text, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, error):
                    winpkg._NSISTemplates(path)
        templates = winpkg._NSISTemplates(winpkg.WIN_PKG_PROJECT_DIR / "installer.nsi.in")
        with self.assertRaisesRegex(ValueError, "missing.*UNKNOWN"):
            templates.render("UNKNOWN")

    def test_optional_installer_fragments_and_output_encoding(self) -> None:
        for architecture in ("amd64", "arm64"):
            for service in (False, True):
                for attachments in (False, True):
                    with self.subTest(architecture=architecture, service=service, attachments=attachments):
                        self._check_installer(architecture, service, attachments)

    def _check_installer(self, architecture: str, service: bool, attachments: bool) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            key = "buckyos" if service else "BuckyOSApp"
            payload = root / "payload"
            (payload / key).mkdir(parents=True)
            license_file = root / "license.rtf"
            vcredist = root / "vcredist.exe"
            if attachments:
                license_file.write_text("fixture", encoding="utf-8")
                vcredist.write_bytes(b"fixture")
            out = root / "installer.nsi"
            winpkg.generate_nsis_script(
                title="BuckyOS 测试", version="0.7.0+test", architecture=architecture,
                components=[winpkg.PublishComponent(
                    key=key, name="BuckyOS", kind="app" if service else "bundle",
                    optional=False, default_selected=True, src=None,
                    default_target="D:\\BuckyOS 数据", system_service=service,
                )],
                payload_dir=payload, out_path=out,
                license_file=license_file, bundled_vcredist=vcredist,
            )
            content = out.read_bytes()
            self.assertTrue(content.startswith(b"\xef\xbb\xbf"))
            self.assertNotIn(b"\r", content.replace(b"\r\n", b""))
            self.assertNotIn(b"\n", content.replace(b"\r\n", b""))
            script = content.decode("utf-8-sig")
            self.assertNotIn("@@", script)
            self.assertIn("BuckyOS 测试", script)
            self.assertIn("BuckyOS 数据", script)
            self.assertEqual('!include "x64.nsh"' in script, architecture == "amd64")
            self.assertEqual("MUI_PAGE_LICENSE" in script, attachments)
            self.assertEqual("Function InstallBundledVCRedist" in script, attachments)
            self.assertEqual("Function LaunchInstalledApplication" in script, not service)
            self.assertEqual("Call StopExistingBuckyOS" in script, service)
            self.assertEqual("Call InstallBundledVCRedist" in script, service and attachments)
            self.assertEqual("silent_vcredist_missing:" in script, service and not attachments)


if __name__ == "__main__":
    unittest.main()
