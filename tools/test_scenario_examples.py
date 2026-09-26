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
            return [p for p in paths if not p.endswith('/agent-run-progress.json')]
        with patch.object(conformance.glob, 'glob', side_effect=missing), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                conformance.main([])
        self.assertEqual(2, error.exception.code)

    def test_all_scenarios_and_renderable_prefixes(self):
        self.assertEqual(set(self.examples), {'form-interaction', 'host-action', 'agent-run-progress', 'agent-run', 'data-report'})
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
        messages = copy.deepcopy(self.examples['agent-run-progress'])[:4]
        messages[3]['updateComponents']['components'] = [
            c for c in messages[3]['updateComponents']['components'] if c['id'] != 'executionTimeline']
        self.assert_valid(messages)

    def test_completed_streaming_retains_both_panels_and_stops_shimmer(self):
        messages = self.examples['agent-run']
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

    def assert_loading_images_cleared(self, component, loading_images):
        for field in ('icon', 'darkIcon'):
            if component.get(field):
                self.assertNotIn(component[field], loading_images)

    def test_completion_may_replace_loading_with_a_status_image(self):
        loading = {'https://example.com/loading-light.gif', 'https://example.com/loading-dark.gif'}
        self.assert_loading_images_cleared({}, loading)
        self.assert_loading_images_cleared({'icon': 'https://example.com/complete.png'}, loading)
        for field in ('icon', 'darkIcon'):
            for image in loading:
                with self.subTest(field=field, image=image):
                    with self.assertRaises(AssertionError):
                        self.assert_loading_images_cleared({field: image}, loading)

    def test_agent_batches_preserve_interaction_and_icon_states(self):
        # These phases belong to the fixed examples, not to the protocol.
        expected_groups = (
            {'toolGroup_read': True},
            {'toolGroup_read': False, 'toolGroup_summary': True},
            {'toolGroup_read': False, 'toolGroup_summary': False},
        )
        for name in ('agent-run-progress', 'agent-run'):
            messages = self.examples[name]
            self.assertEqual(4 if name == 'agent-run-progress' else 5, len(messages))
            active = {}
            loading_images = set()
            for batch, message in enumerate(messages[2:]):
                components = message['updateComponents']['components']
                for component in components:
                    if component['id'] in active:
                        self.assertNotIn('defaultExpanded', component)
                    elif component['component'] == 'CollapsiblePanel':
                        self.assertIs(component.get('defaultExpanded'), False)
                    active[component['id']] = component
                panel = active['executionPanel']
                self.assertNotIn('icon', panel)
                self.assertNotIn('darkIcon', panel)
                self.assertEqual('reasoning', panel['variant'])
                self.assertTrue(active['executionTimeline']['children'])
                self.assertEqual(batch < 2, panel.get('textEffect') == 'shimmer')
                self.assertEqual(batch < 2, panel['title'].startswith('[AIGenerating]'))
                self.assertEqual(list(expected_groups[batch]), active['executionTimeline']['children'])
                for group_id, running in expected_groups[batch].items():
                    with self.subTest(example=name, batch=batch, group=group_id):
                        group = active[group_id]
                        self.assertTrue(group['title'].strip())
                        if running:
                            for field in ('icon', 'darkIcon'):
                                self.assertTrue(group[field].startswith('https://'))
                                self.assertTrue(group[field].endswith('.gif'))
                            self.assertNotEqual(group['icon'], group['darkIcon'])
                            loading_images.update((group['icon'], group['darkIcon']))
                        else:
                            self.assert_loading_images_cleared(group, loading_images)
                for component in active.values():
                    if component['component'] in ('Text', 'Icon', 'CollapsiblePanel'):
                        self.assertEqual('common_level2_base_color', component['colorToken'])
                    if component['component'] == 'Text':
                        self.assertEqual('common_action_text_style__font_size', component['sizeToken'])
                        self.assertIs(component['bold'], False)
                    if component['component'] != 'CollapsiblePanel' or component['id'] == 'executionPanel':
                        continue
                    self.assertEqual('indented', component['variant'])
                    self.assertNotIn('textEffect', component)
                    self.assertNotIn('[AIGenerating]', component['title'])
                    self.assertTrue(component['children'])
                has_result = name == 'agent-run' and batch == 2
                self.assertEqual(has_result, 'result' in active['root']['children'])
                self.assertEqual(has_result, any(c['component'] == 'File' for c in active.values()))
            self.assertEqual('Folder_L_outlined', active['toolGroup_read_icon']['name'])
            self.assertEqual('CodeProgram_L_outlined', active['toolGroup_summary_icon']['name'])
            self.assertEqual(name == 'agent-run-progress',
                             any(c.get(field) in loading_images for c in active.values()
                                 for field in ('icon', 'darkIcon')))
            if name == 'agent-run-progress':
                self.assertEqual(['executionPanel'], active['root']['children'])
            if name == 'agent-run':
                self.assertEqual(['answer', 'artifact'], active['result']['children'])
                self.assertNotIn('result', active['executionTimeline']['children'])

    def test_bound_content_subpath_update(self):
        # Protocol regression coverage, independent of explanatory Markdown.
        surface_id = 'bound-content-test'
        batches = [[
            {'version': 'v1.0', 'updateDataModel': {'surfaceId': surface_id,
             'path': '/execution/summary', 'value': 'Reading...'}},
            {'version': 'v1.0', 'updateComponents': {'surfaceId': surface_id, 'components': [
                {'id': 'stepSummary', 'component': 'Markdown',
                 'content': {'path': '/execution/summary'}}]}}
        ], [
            {'version': 'v1.0', 'updateDataModel': {'surfaceId': surface_id,
             'path': '/execution/summary', 'value': 'Read complete.'}}
        ]]
        for batch in batches:
            self.assertEqual([], self.assert_valid(batch))
        initial, delta = batches
        binding = initial[1]['updateComponents']['components'][0]['content']['path']
        self.assertEqual(binding, initial[0]['updateDataModel']['path'])
        self.assertEqual(binding, delta[0]['updateDataModel']['path'])
        self.assertIsInstance(initial[0]['updateDataModel']['value'], str)
        self.assertIsInstance(delta[0]['updateDataModel']['value'], str)
        self.assertNotEqual(initial[0]['updateDataModel']['value'], delta[0]['updateDataModel']['value'])
        surface_id = initial[0]['updateDataModel']['surfaceId']
        messages = [
            {'version': 'v1.0', 'createSurface': {'surfaceId': surface_id,
             'catalogId': self.protocol.docs['catalog-components-common.json']['catalogId']}},
            {'version': 'v1.0', 'updateDataModel': {'surfaceId': surface_id, 'path': '/', 'value': {}}},
            {'version': 'v1.0', 'updateComponents': {'surfaceId': surface_id, 'components': [
                {'id': 'root', 'component': 'Column', 'children': ['stepSummary']}]}}
        ] + initial + delta
        self.assertEqual([], self.assert_valid(messages))
        self.assertTrue(preflight(messages, self.protocol)['valid'])

    def assert_agent_result_timing(self, messages):
        # Static reachability for this fixed example, not client visibility or a protocol rule.
        active = {}
        first_reachable = {}
        for batch, message in enumerate(messages[2:]):
            for component in message['updateComponents']['components']:
                active[component['id']] = component
            reachable = set()
            pending = ['root']
            while pending:
                component_id = pending.pop()
                if component_id in reachable:
                    continue
                self.assertIn(component_id, active)
                reachable.add(component_id)
                children = active[component_id].get('children', [])
                self.assertIsInstance(children, list)
                pending.extend(children)
            for component_id in ('answer', 'artifact'):
                if component_id in reachable:
                    first_reachable.setdefault(component_id, batch)
            expected = {'answer', 'artifact'} if batch == len(messages) - 3 else set()
            self.assertEqual(expected, reachable & {'answer', 'artifact'})
        self.assertEqual({'answer': len(messages) - 3, 'artifact': len(messages) - 3},
                         first_reachable)
        self.assertEqual('Markdown', active['answer']['component'])
        self.assertEqual('File', active['artifact']['component'])

    def test_agent_results_first_reachable_together_in_final_batch(self):
        self.assert_agent_result_timing(self.examples['agent-run'])

    def test_agent_result_timing_rejects_early_or_detached_results(self):
        for component_id in ('answer', 'artifact'):
            for mutation in ('early_direct', 'early_nested', 'detached_final'):
                with self.subTest(component=component_id, mutation=mutation):
                    messages = copy.deepcopy(self.examples['agent-run'])
                    initial = messages[2]['updateComponents']['components']
                    final = messages[-1]['updateComponents']['components']
                    if mutation == 'detached_final':
                        next(c for c in final if c['id'] == 'result')['children'].remove(component_id)
                    else:
                        initial.append(copy.deepcopy(next(c for c in final if c['id'] == component_id)))
                        target = component_id
                        if mutation == 'early_nested':
                            target = 'earlyResult'
                            initial.append({'id': target, 'component': 'Column', 'children': [component_id]})
                        next(c for c in initial if c['id'] == 'root')['children'].append(target)
                    with self.assertRaises(AssertionError):
                        self.assert_agent_result_timing(messages)

    def test_reasoning_panel_allows_rich_content_and_artifacts_while_running(self):
        # The public example's result placement is not a protocol restriction.
        messages = copy.deepcopy(self.examples['agent-run-progress'])
        result_components = copy.deepcopy([
            c for c in self.examples['agent-run'][-1]['updateComponents']['components']
            if c['id'] in ('result', 'answer', 'artifact')])
        components = messages[-1]['updateComponents']['components']
        timeline = next(c for c in components if c['id'] == 'executionTimeline')
        timeline['children'].append('result')
        components.extend(result_components)
        self.assertEqual([], self.assert_valid(messages))
        self.assertTrue(preflight(messages, self.protocol)['valid'])
        panel = next(c for c in components if c['id'] == 'executionPanel')
        self.assertEqual('shimmer', panel['textEffect'])
        self.assertTrue(panel['title'].startswith('[AIGenerating]'))

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
