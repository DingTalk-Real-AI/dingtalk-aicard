#!/usr/bin/env python3
# Copyright 2026 Alibaba Group
# Licensed under the Apache License, Version 2.0.

"""Regress initialization, resources, and lookup compatibility without sending cards."""
import copy, importlib.util, json, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
s = importlib.util.spec_from_file_location('lint', ROOT / 'skills/dingtalk-aicard/scripts/aicard_lint.py')
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)

class OptimizationTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.p = m.Protocol(str(ROOT / 'skills/dingtalk-aicard/references/protocol'))

    def setUp(self):
        self.create = {'version': 'v1.0', 'createSurface': {'surfaceId': 's', 'catalogId': self.p.docs['catalog-components-common.json']['catalogId']}}
        self.data = {'version': 'v1.0', 'updateDataModel': {'surfaceId': 's', 'path': '/', 'value': {}}}
        self.components = {'version': 'v1.0', 'updateComponents': {'surfaceId': 's', 'components': [{'id': 'root', 'component': 'Text', 'text': '测试'}]}}
        self.card = [self.create, self.data, self.components]

    def codes(self, card, mode='new-card'):
        return {d['code'] for d in m.preflight(card, self.p, mode)['diagnostics']}

    def test_missing_initialization_is_not_a_schema_error(self):
        card = [self.create, self.components]
        self.assertEqual([], m.lint(card, self.p)[0])
        self.assertIn('delivery.data_model_missing', self.codes(card))

    def test_data_order_multiple_writes_and_literal_content(self):
        for card in [self.card, [self.create, self.components, self.data], self.card + [self.data]]:
            self.assertFalse(self.codes(card))

    def test_inline_initialization_remains_compatible(self):
        self.create['createSurface'].update(dataModel={}, components=self.components['updateComponents']['components'])
        self.assertEqual([], m.lint([self.create], self.p)[0])
        self.assertFalse(self.codes([self.create]))
        self.create['createSurface']['components'] = [{'id': 'root', 'component': 'Image', 'url': 'data:image/png;base64,error!'}]
        self.assertIn('resource.inline_data_unsupported', self.codes([self.create]))

    def test_deletion_is_not_initialization(self):
        del self.data['updateDataModel']['value']
        self.assertIn('delivery.data_model_missing', self.codes(self.card))

    def test_nonroot_write_initializes_data(self):
        self.data['updateDataModel'].update(path='/name', value='测试')
        self.assertFalse(self.codes(self.card))

    def test_cross_surface_and_lifecycle(self):
        self.data['updateDataModel']['surfaceId'] = 'other'
        self.assertIn('delivery.surface_not_created', self.codes(self.card))
        self.assertIn('delivery.unsupported_create_operation', self.codes(self.card + [{'version': 'v1.0', 'deleteSurface': {'surfaceId': 's'}}]))

    def test_delivery_requires_explicit_data_path_without_changing_schema(self):
        self.data['updateDataModel'].pop('path')
        self.assertEqual([], m.lint(self.card,self.p)[0])
        self.assertIn('delivery.data_model_path', self.codes(self.card))

    def test_delta_resource_check_does_not_require_creation(self):
        self.assertFalse(self.codes([self.components], 'resources'))

    def test_resource_policy_rejects_data_without_rejecting_other_schemes(self):
        c = self.components['updateComponents']['components'][0]
        c.update(component='Image')
        c.pop('text')
        for url in ['data:image/jpeg;base64,/Users/test: error\n/9j/', 'data:image/png;base64,', 'data:image/png;base64,%GG', 'data:image/svg+xml,%3Csvg/%3E']:
            c['url'] = url
            self.assertFalse(m.preflight(self.card, self.p)['valid'])
        for url in ['dingtalk://some-media', 'https://example.com/preview.png']:
            c['url'] = url
            self.assertFalse(self.codes(self.card))

    def test_inline_data_is_rejected_in_resource_fields(self):
        for field in ['url', 'darkUrl', 'imageUrl', 'posterUrl', 'coverUrl', 'images']:
            with self.subTest(field=field):
                c = self.components['updateComponents']['components'][0]
                c.clear()
                value = 'data:image/png;base64,YQ=='
                c.update(id='root', component='Image', **{field: [value] if field == 'images' else value})
                for card, mode in ((self.card, 'new-card'), ([self.components], 'resources')):
                    result = m.preflight(card, self.p, mode)
                    self.assertFalse(result['valid'])
                    self.assertFalse(result['renderingVerified'])
                    self.assertEqual(1, len(result['diagnostics']))
                    error = result['diagnostics'][0]
                    self.assertEqual('resource.inline_data_unsupported', error['code'])
                    self.assertEqual('error', error['severity'])
                    self.assertTrue(error['pointer'].endswith('/' + field + ('/0' if field == 'images' else '')))

    def test_shared_resource_policy_cases_preserve_schema_validity(self):
        path = ROOT / 'dws-aicard/internal/card/a2ui/testdata/resource-policy.json'
        for case in json.loads(path.read_text()):
            with self.subTest(name=case['name']):
                self.components['updateComponents']['components'] = [case['component']]
                self.assertEqual([], m.lint(self.card, self.p)[0])
                for card, mode in ((self.card, 'new-card'), ([self.components], 'resources')):
                    result = m.preflight(card, self.p, mode)
                    self.assertEqual(case['valid'], result['valid'])
                    if case.get('warningPointerSuffix'):
                        self.assertEqual(1, len(result['diagnostics']))
                        warning = result['diagnostics'][0]
                        self.assertEqual('resource.markdown_data_unverified', warning['code'])
                        self.assertEqual('warning', warning['severity'])
                        self.assertTrue(warning['pointer'].endswith(case['warningPointerSuffix']))
                    elif case['valid']:
                        self.assertEqual([], result['diagnostics'])
                    else:
                        self.assertEqual(1, len(result['diagnostics']))
                        error = result['diagnostics'][0]
                        self.assertEqual('resource.inline_data_unsupported', error['code'])
                        self.assertEqual('error', error['severity'])
                        self.assertTrue(error['pointer'].endswith(case['pointerSuffix']))

    def test_markdown_review_does_not_block_adversarial_content(self):
        # Exercise the full preflight on large syntax-like text without parsing it.
        import time
        shapes = {
            'image openers': '![' * 30_000,
            'unclosed tags': '<img a' * 10_000,
            'reference openers': '![a][' * 12_000,
            'backtick runs': '` ``' * 15_000,
            'reference definitions': '[a]: ' * 12_000,
            'shared bracket before a long destination': '![' * 15_000 + '](' + 'a' * 30_000,
            'shared bracket with an inline reference': '[x]: data:a\n\n' + '![' * 30_000 + ']',
            'tags sharing one close': '<img ' * 12_000 + '>',
            'attributes sharing one close': '<img src=a ' * 6_000 + '>',
            'overlapping destinations': '![a](x' * 10_000,
            'overlapping entity destinations': '![a](&' * 10_000,
            'backticks between blank lines': '`\n\n' * 20_000,
            'nested labels with an unused reference': '![' * 30_000 + ']' * 30_000 + '\n\n[x]: data:image/png;base64,AA==',
        }
        for name, content in shapes.items():
            with self.subTest(shape=name):
                started = time.perf_counter()
                self.components['updateComponents']['components'] = [{'id': 'root', 'component': 'Markdown', 'content': content}]
                result = m.preflight(self.card, self.p)
                self.assertTrue(result['valid'])
                self.assertFalse(any(d['severity'] == 'error' for d in result['diagnostics']))
                self.assertLess(time.perf_counter() - started, 1.0)

    def test_text_is_not_a_resource(self):
        self.components['updateComponents']['components'][0]['text'] = 'data:image/png;base64,invalid!'
        self.assertFalse(self.codes(self.card))

    def test_compact_bundle_roundtrips_without_changing_single_explain(self):
        names = ['Text', 'Column', 'Row', 'ButtonGroup', 'CheckBox', 'Tabs', 'promptText']
        before = [m.explain(self.p, n) for n in names]
        packed = m.explain_many(self.p, names, True)
        defs = packed['definitions']

        def unpack(v):
            if isinstance(v, dict):
                return unpack(defs[v['$contractRef']]) if set(v) == {'$contractRef'} else {k: unpack(x) for k, x in v.items()}
            if isinstance(v, list):
                return [unpack(x) for x in v]
            return v
        self.assertEqual(before, unpack(packed['contracts']))
        self.assertEqual(before, [m.explain(self.p, n) for n in names])
        self.assertLess(len(json.dumps(packed)), len(json.dumps(before)))

    def test_unknown_name_stays_visible(self):
        self.assertEqual('unknown', m.explain_many(self.p, ['Text', 'NoSuchName'])['contracts'][1]['kind'])
if __name__ == '__main__':
    unittest.main()
