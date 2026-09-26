"""Check authoring action contracts without claiming client execution."""
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/dingtalk-aicard/scripts'))
from aicard_lint import Protocol, lint, preflight


class ReferenceActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol = Protocol(str(ROOT / 'skills/dingtalk-aicard/references/protocol'))

    def setUp(self):
        self.button = {
            'id': 'root', 'component': 'Button', 'child': 'label',
            'variant': 'primary', 'disabled': False,
            'action': {'functionCall': {'call': 'openUrl',
                                       'args': {'url': 'https://example.com'}}},
        }
        self.messages = [
            {'version': 'v1.0', 'createSurface': {
                'surfaceId': 'action-test',
                'catalogId': self.protocol.docs['catalog-components-common.json']['catalogId']}},
            {'version': 'v1.0', 'updateDataModel': {
                'surfaceId': 'action-test', 'path': '/', 'value': {}}},
            {'version': 'v1.0', 'updateComponents': {
                'surfaceId': 'action-test', 'components': [self.button,
                    {'id': 'label', 'component': 'Text', 'text': 'View details'}]}},
        ]

    def test_navigation_does_not_require_or_inject_form_checks(self):
        before = copy.deepcopy(self.messages)
        diagnostics, _ = lint(self.messages, self.protocol)
        self.assertFalse([d for d in diagnostics if d['severity'] == 'error'])
        self.assertTrue(preflight(self.messages, self.protocol)['valid'])
        self.assertEqual(before, self.messages)
        self.assertNotIn('checks', self.button)

    def test_explicit_disabled_state_and_checks_are_preserved(self):
        self.button['disabled'] = True
        self.button['checks'] = [{'condition': False, 'message': 'Unavailable'}]
        before = copy.deepcopy(self.messages)
        diagnostics, _ = lint(self.messages, self.protocol)
        self.assertFalse([d for d in diagnostics if d['severity'] == 'error'])
        self.assertEqual(before, self.messages)

    def test_navigation_requires_destination_argument(self):
        self.button['action']['functionCall']['args'] = {}
        diagnostics, _ = lint(self.messages, self.protocol)
        self.assertTrue([d for d in diagnostics if d['severity'] == 'error'])


if __name__ == '__main__':
    unittest.main()
