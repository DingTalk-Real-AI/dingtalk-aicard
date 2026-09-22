#!/usr/bin/env python3
# Copyright 2026 Alibaba Group
# Licensed under the Apache License, Version 2.0.

"""Check public scenario structure and content without inferring runtime behavior."""
import copy
import contextlib
import io
from unittest.mock import patch
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'skills/dingtalk-aicard/scripts'))
from aicard_lint import Protocol, lint, preflight


class ScenarioExamplesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol = Protocol(str(ROOT / 'skills/dingtalk-aicard/references/protocol'))
        cls.examples = {p.stem: json.loads(p.read_text())
                        for p in (ROOT / 'skills/dingtalk-aicard/references/protocol/examples').glob('*.json')}

    def diagnostics(self, messages):
        return lint(messages, self.protocol)[0]

    def assert_valid(self, messages):
        diagnostics = self.diagnostics(messages)
        self.assertFalse([d for d in diagnostics if d['severity'] == 'error'], diagnostics)
        return diagnostics

    def test_conformance_rejects_missing_scenario_before_running_commands(self):
        import conformance
        original = conformance.glob.glob
        def missing(pattern):
            paths = original(pattern)
            return [p for p in paths if not p.endswith('/agent-progress.json')]
        with patch.object(conformance.glob, 'glob', side_effect=missing), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                conformance.main([])
        self.assertEqual(2, error.exception.code)

    def test_all_scenarios_and_renderable_prefixes(self):
        self.assertEqual(set(self.examples), {'form-interaction', 'host-action', 'agent-progress', 'data-report'})
        for name, messages in self.examples.items():
            for end in range(1, len(messages) + 1):
                if not any(m.get('updateComponents', {}).get('components') for m in messages[:end]):
                    continue
                with self.subTest(name=name, end=end):
                    diagnostics = self.assert_valid(messages[:end])
                    self.assertEqual([], diagnostics)

    def test_each_example_is_a_complete_new_card(self):
        catalog_id = self.protocol.docs['catalog-components-common.json']['catalogId']
        for name, messages in self.examples.items():
            with self.subTest(name=name):
                self.assertEqual(['createSurface', 'updateDataModel', 'updateComponents'],
                                 [next(key for key in message if key != 'version') for message in messages[:3]])
                surface_id = messages[0]['createSurface']['surfaceId']
                self.assertEqual(catalog_id, messages[0]['createSurface']['catalogId'])
                self.assertEqual('/', messages[1]['updateDataModel']['path'])
                self.assertTrue(all(next(value for key, value in message.items() if key != 'version')
                                    ['surfaceId'] == surface_id for message in messages))
                self.assert_valid(messages)
                self.assertTrue(preflight(messages, self.protocol)['valid'])

    def test_structure_checks_do_not_judge_initial_values(self):
        messages = copy.deepcopy(self.examples['form-interaction'])
        messages[1]['updateDataModel']['value']['form']['dept'] = None
        self.assert_valid(messages)
        messages.append({'version': 'v1.0', 'updateDataModel': {
            'surfaceId': 'open-form', 'path': '/form/dept', 'value': []}})
        self.assert_valid(messages)
        self.assert_valid(messages[:-1])

    def test_structure_checks_do_not_resolve_step_references(self):
        messages = copy.deepcopy(self.examples['agent-progress'])[:4]
        messages[3]['updateComponents']['components'] = [
            c for c in messages[3]['updateComponents']['components'] if c['id'] != 'executionTimeline']
        self.assert_valid(messages)

    def test_completed_progress_retains_both_panels_and_stops_shimmer(self):
        messages = self.examples['agent-progress']
        active = {}
        initial_shimmer = False
        for message in messages:
            for component in message.get('updateComponents', {}).get('components', []):
                initial_shimmer |= component.get('textEffect') == 'shimmer'
                active[component['id']] = component
        self.assertTrue(initial_shimmer)
        self.assertEqual('reasoning', active['executionPanel']['variant'])
        self.assertIn('toolGroup_summary', active['executionTimeline']['children'])
        self.assertFalse(any(c.get('textEffect') for c in active.values()))

    def test_host_initial_success_and_failure_state(self):
        original = self.examples['host-action']
        initial = self.assert_valid(original)
        self.assertEqual([], initial)
        success = original + [{'version': 'v1.0', 'updateDataModel': {
            'surfaceId': 'open-host-action', 'path': '/ui/' + slot,
            'value': {'status': 'success', 'data': {'text': '已确认的内容'}}}}
            for slot in ('prompt', 'title', 'note')]
        self.assertEqual([], self.assert_valid(success))
        # Cancel writes nothing and keeps the last successful result; failure has no data.text.
        failed = success + [{'version': 'v1.0', 'updateDataModel': {
            'surfaceId': 'open-host-action', 'path': '/ui/prompt', 'value': {'status': 'failed'}}}]
        self.assertEqual([], self.assert_valid(failed))

    def test_host_catalog_and_metadata_version_are_enforced(self):
        for mutation in ('catalog', 'metadata'):
            messages = copy.deepcopy(self.examples['host-action'])
            button = next(c for c in messages[2]['updateComponents']['components'] if c['id'] == 'edit')
            if mutation == 'catalog':
                del button['action']['functionCall']['catalogId']
            else:
                extension = button['metadata']['extensions']
                extension['dt_actionBindingsV2'] = extension.pop('dt_actionBindingsV1')
            with self.subTest(mutation=mutation):
                self.assertTrue(any(d['severity'] == 'error' for d in self.diagnostics(messages)))

    def test_structure_checks_do_not_resolve_report_bindings(self):
        messages = copy.deepcopy(self.examples['data-report'])
        data = messages[1]['updateDataModel']['value']
        data['table'] = data['table']['data']
        self.assert_valid(messages)
        messages = copy.deepcopy(self.examples['data-report'])
        messages[1]['updateDataModel']['value']['rows'][1]['name'] = 42
        self.assert_valid(messages)


if __name__ == '__main__':
    unittest.main()
