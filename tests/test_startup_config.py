import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class StartupConfigTests(unittest.TestCase):
    def check_hook(self, hook, command):
        text = (ROOT / hook).read_text()
        windows = hook.endswith('.ps1')
        start = text.index('$startupConfigSource = ' if windows else 'START_CONFIG_SOURCE=')
        end = text.index('$TaskName = ' if windows else ('set +e\nstart_buckyos_runtime' if 'macos' in hook else 'cat <<EOF'), start)
        block = text[start:end]
        for state in ('fresh', 'old', 'current', 'identical', 'conflict', 'link'):
            with self.subTest(hook=hook, state=state), tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / 'root with spaces'
                (target / 'etc').mkdir(parents=True)
                old, new = target / 'etc/start_config.json', target / 'security/start_config.json'
                contents = b'{"admin_password_hash":"retained","zone_name":"test"}'
                if state in ('old', 'identical', 'conflict'):
                    old.write_bytes(contents)
                if state in ('current', 'identical', 'conflict'):
                    new.parent.mkdir()
                    new.write_bytes(b'different' if state == 'conflict' else contents)
                if state == 'link':
                    outside = Path(tmp) / 'outside'
                    outside.write_bytes(contents)
                    old.symlink_to(outside)
                script = Path(tmp) / ('hook.ps1' if windows else 'hook.sh')
                prefix = 'param([string]$Root)\n$ErrorActionPreference = "Stop"\n' if windows else 'set -e\n'
                script.write_text(prefix + block)
                env = {**os.environ, 'BUCKYOS_ROOT': str(target)}
                env.pop('BUCKYOS_SECURITY_ROOT', None)
                result = subprocess.run([*command, str(script), *([str(target)] if windows else [])],
                                        env=env, capture_output=True, text=True)
                if state in ('conflict', 'link'):
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(old.read_bytes(), contents)
                    if state == 'conflict':
                        self.assertEqual(new.read_bytes(), b'different')
                    continue
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse(old.exists())
                if state != 'fresh':
                    self.assertEqual(new.read_bytes(), contents)
                    if not windows and state != 'current':
                        self.assertEqual(stat.S_IMODE(new.stat().st_mode), 0o600)
                again = subprocess.run([*command, str(script), *([str(target)] if windows else [])],
                                       env=env, capture_output=True, text=True)
                self.assertEqual(again.returncode, 0, again.stdout + again.stderr)

    def test_deb_postinstall(self):
        self.check_hook('deb_pkg/buckyos_postinstall', ['bash'])

    def test_rpm_postinstall(self):
        self.check_hook('rpm_pkg/buckyos_postinstall', ['bash'])

    @unittest.skipUnless(shutil.which('zsh'), 'zsh is required for macOS hook checks')
    def test_macos_postinstall(self):
        self.check_hook('macos_pkg/scripts/buckyos_postinstall', ['zsh'])

    @unittest.skipUnless(shutil.which('pwsh'), 'PowerShell is required for Windows hook checks')
    def test_windows_postinstall(self):
        self.check_hook('win_pkg/scripts/buckyos_postinstall.ps1', ['pwsh', '-NoProfile', '-File'])


if __name__ == '__main__':
    unittest.main()
