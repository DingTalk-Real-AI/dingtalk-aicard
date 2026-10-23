#!/usr/bin/env python3
# Copyright 2026 Alibaba Group
# Licensed under the Apache License, Version 2.0.

"""Use shared reference preflight cases for Python and Go without changing schema rules."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/dingtalk-aicard/scripts'))
import aicard_lint as lint

# Case names are English; localized card text intentionally exercises Unicode payloads.
FIXTURES = ROOT / 'dws-aicard/internal/card/a2ui/testdata/preflight-references.json'


class ReferencePreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol = lint.Protocol(lint.PROTOCOL_DIR)
        cls.cases = json.loads(FIXTURES.read_text(encoding='utf-8'))

    def test_shared_cases(self):
        for case in self.cases:
            with self.subTest(name=case['name']):
                original = copy.deepcopy(case['messages'])
                self.assertEqual([], lint.lint(original, self.protocol)[0])
                result = lint.preflight(original, self.protocol, case['mode'])
                self.assertEqual(case['valid'], result['valid'], result)
                project = lambda ds: sorted((d['code'], d['severity'], d['pointer']) for d in ds)
                self.assertEqual(project(case['diagnostics']), project(result['diagnostics']))
                self.assertEqual(case['messages'], original)
                self.assertFalse(result['renderingVerified'])

    def test_cli_warning_succeeds_and_reference_error_keeps_schema_valid(self):
        for name, code in [('Unreachable component is a warning', 0), ('Missing static child reference', 1),
                           ('Repeated child under one parent is an error', 1),
                           ('Reported selection card attaches submit_text to root and Button', 1),
                           ('Reported selection card with only extra root reference removed', 0)]:
            case = next(c for c in self.cases if c['name'] == name)
            with self.subTest(name=name), tempfile.TemporaryDirectory(dir=ROOT) as temp:
                path = Path(temp) / 'card.json'
                path.write_text(json.dumps(case['messages']), encoding='utf-8')
                proc = subprocess.run([sys.executable, '-B', str(ROOT / 'skills/dingtalk-aicard/scripts/aicard_lint.py'),
                                       str(path), '--preflight', 'new-card', '--format', 'json'],
                                      capture_output=True, encoding='utf-8', timeout=30)
                self.assertEqual(code, proc.returncode, proc.stderr)
                result = json.loads(proc.stdout)
                self.assertTrue(result['valid'])
                self.assertEqual(code == 0, result['preflight']['valid'])

    def test_deep_static_graph_uses_iterative_traversal(self):
        messages = copy.deepcopy(self.cases[0]['messages'])
        nodes = [{'id': 'root' if i == 0 else str(i), 'component': 'Column',
                  'children': [str(i + 1)]} for i in range(1500)]
        nodes.append({'id': '1500', 'component': 'Text', 'text': 'Last node'})
        messages[-1]['updateComponents']['components'] = nodes
        self.assertEqual([], lint.preflight(messages, self.protocol)['diagnostics'])

    def test_reported_card_repair_changes_only_the_extra_root_reference(self):
        broken = next(c for c in self.cases if c['name'] ==
                      'Reported selection card attaches submit_text to root and Button')
        fixed = next(c for c in self.cases if c['name'] ==
                     'Reported selection card with only extra root reference removed')
        repaired = copy.deepcopy(broken['messages'])
        components = repaired[1]['updateComponents']['components']
        self.assertEqual(11, len(components))
        self.assertEqual('submit_text', components[9]['child'])
        components[0]['children'].remove('submit_text')
        self.assertEqual(repaired, fixed['messages'])
        diagnostics = lint.preflight(broken['messages'], self.protocol)['diagnostics']
        self.assertEqual(1, len(diagnostics))
        for node in ('root', 'submit_btn', 'submit_text'):
            self.assertIn(node, diagnostics[0]['message'])
        self.assertEqual('/1/updateComponents/components/0/children/9', diagnostics[0]['pointer'])
        for pointer in ('/1/updateComponents/components/0/children/9',
                        '/1/updateComponents/components/9/child'):
            self.assertIn(pointer, diagnostics[0]['message'])

    def test_dfs_pointer_does_not_depend_on_parent_id_sorting(self):
        case = next(c for c in self.cases if c['name'] ==
                    'Reported selection card keeps DFS pointer when Button ID is renamed')
        diagnostics = lint.preflight(case['messages'], self.protocol)['diagnostics']
        self.assertEqual(1, len(diagnostics))
        self.assertEqual('/1/updateComponents/components/0/children/9', diagnostics[0]['pointer'])
        self.assertIn('a_submit_btn (/1/updateComponents/components/9/child)', diagnostics[0]['message'])

    def test_many_overlapping_template_sources_visit_each_edge_a_bounded_number_of_times(self):
        class CountedEdges(list):
            visits = 0

            def __iter__(self):
                for edge in super().__iter__():
                    CountedEdges.visits += 1
                    yield edge

            def __reversed__(self):
                for edge in super().__reversed__():
                    CountedEdges.visits += 1
                    yield edge

        size = 3000
        nodes = {'root': ({}, '/components/root')}
        edges = {'root': CountedEdges()}
        for index in range(size):
            owner, source = f'container_{index}', f'source_{index}'
            nodes[owner], nodes[source] = ({}, '/components/' + owner), ({}, '/components/' + source)
            edges['root'].append((owner, f'/root/children/{index}', False))
            edges[owner] = CountedEdges([(source, f'/{owner}/children/componentId', True)])
            edges[source] = CountedEdges(
                [(f'source_{index + 1}', f'/{source}/children/0', False)]
                if index + 1 < size else [])
        diagnostics = lint._check_static_ownership(nodes, edges, 'scale')
        self.assertEqual(size - 1, len(diagnostics))
        self.assertTrue(all(d['code'] == 'reference.multiple_parents' for d in diagnostics))
        edge_count = sum(len(children) for children in edges.values())
        # Count operations rather than wall-clock time to avoid a machine-speed gate.
        self.assertLessEqual(CountedEdges.visits, 6 * edge_count)



if __name__ == '__main__':
    unittest.main()
