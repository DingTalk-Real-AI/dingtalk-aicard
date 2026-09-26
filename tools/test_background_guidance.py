"""Guard background guidance, protocol behavior, and synchronized distribution."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'skills/dingtalk-aicard'
TARGET = ROOT / 'dws-aicard/skills/multi/dingtalk-aicard'


class BackgroundGuidanceTests(unittest.TestCase):
    def test_background_guidance_is_synchronized(self):
        relative = Path('references/design.md')
        self.assertEqual((SOURCE / relative).read_bytes(),
                         (TARGET / relative).read_bytes())


class BackgroundProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("background_aicard_lint", SOURCE / "scripts/aicard_lint.py")
        cls.lint = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.lint)
        cls.protocol = cls.lint.Protocol(SOURCE / "references/protocol")

    def diagnostics(self, style):
        payload = [{"version": "v1.0", "createSurface": {
            "surfaceId": "background-contract", "catalogId": "https://dingtalk.com/card/a2ui/catalogs/public/catalog.json"}},
            {"version": "v1.0", "updateComponents": {"surfaceId": "background-contract", "components": [
                {"id": "root", "component": "Card", "child": "body", **style},
                {"id": "body", "component": "Text", "text": "Synthetic background example"},
            ]}}]
        return self.lint.lint(payload, self.protocol)[0]

    def test_transparent_opaque_themed_and_default_roots_are_protocol_valid(self):
        styles = ({"backgroundColor": "#00FFFFFF"}, {"backgroundColor": "#FFFFFFFF"},
                  {"backgroundColorToken": "common_bg_color"}, {})
        for style in styles:
            with self.subTest(style=style):
                errors = [item for item in self.diagnostics(style) if item["severity"] == "error"]
                self.assertEqual(errors, [])

    def test_invalid_color_type_still_fails_the_protocol(self):
        errors = [item for item in self.diagnostics({"backgroundColor": 42}) if item["severity"] == "error"]
        self.assertTrue(errors)


if __name__ == '__main__':
    unittest.main()
