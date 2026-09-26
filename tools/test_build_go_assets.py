"""Check deterministic bundle generation and stale artifact rejection."""
import unittest
import hashlib
import tempfile
from pathlib import Path
from unittest.mock import patch
import build_go_assets as assets

class GoAssetsTests(unittest.TestCase):
    def test_bundle_does_not_depend_on_input_order(self):
        contracts = {'Text': {'name': 'Text', 'kind': 'component'}, 'Chart': {'name': 'Chart', 'kind': 'component'}}
        self.assertEqual(assets.serialized(assets.build_explain_bundle(contracts, {})),
                         assets.serialized(assets.build_explain_bundle(dict(reversed(list(contracts.items()))), {})))

    def test_validator_assets_bind_the_exact_explain_bytes(self):
        validator, bundle = assets.generate_all()
        self.assertEqual(hashlib.sha256(assets.serialized(bundle)).hexdigest(), validator['explainSha256'])
        altered = assets.serialized(bundle) + b' '
        self.assertNotEqual(hashlib.sha256(altered).hexdigest(), validator['explainSha256'])

    def test_generated_inventory_is_exact(self):
        self.assertEqual(0, assets.main(['--check']))

    def test_missing_modified_and_legacy_files_fail_check(self):
        with tempfile.TemporaryDirectory(dir=assets.ROOT) as directory:
            output = Path(directory) / 'assets.json'
            explain = Path(directory) / 'explain.json'
            with patch.object(assets, 'OUTPUT', output), patch.object(assets, 'EXPLAIN_FILE', explain):
                self.assertEqual(0, assets.main([]))
                self.assertEqual(0, assets.main(['--check']))
                original = explain.read_bytes()
                explain.unlink()
                self.assertEqual(1, assets.main(['--check']))
                explain.write_bytes(original + b' ')
                self.assertEqual(1, assets.main(['--check']))
                explain.write_bytes(original)
                legacy = Path(directory) / 'explain'
                legacy.mkdir()
                (legacy / 'old.json').write_text('{}')
                self.assertEqual(1, assets.main(['--check']))

if __name__ == '__main__':
    unittest.main()
