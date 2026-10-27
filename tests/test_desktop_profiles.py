import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import package_common as common


class DesktopProfileStagingTests(unittest.TestCase):
    def test_both_release_profiles_and_script_manifest_survive_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root/'source/etc/product-profiles'
            folder.mkdir(parents=True)
            for edition in ('full', 'lite'):
                (folder/(edition+'.json')).write_text(json.dumps({'schema_version': 1, 'edition': edition,
                    'capabilities': {'container': edition == 'full'}, 'preinstalled_script_apps': [{'version': 'release-owned'}]}))
            (folder.parent/'product.json').write_text(json.dumps({'edition': 'full'}))
            common.stage_desktop_profiles(root/'source', root/'package')
            for edition in ('full', 'lite'):
                self.assertEqual((root/'package/etc/product-profiles'/(edition+'.json')).read_bytes(), (folder/(edition+'.json')).read_bytes())
            (folder/'lite.json').unlink()
            with self.assertRaises(FileNotFoundError):
                common.stage_desktop_profiles(root/'source', root/'missing')
            (folder.parent/'product.json').write_text(json.dumps({'edition': 'lite'}))
            with self.assertRaisesRegex(ValueError, 'Full stage'):
                common.stage_desktop_profiles(root/'source', root/'lite')
