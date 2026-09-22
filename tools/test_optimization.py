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
        self.assertIn('resource.invalid_base64', self.codes([self.create]))

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

    def test_resource_validation_does_not_reject_unknown_schemes(self):
        c = self.components['updateComponents']['components'][0]
        c.update(component='Image')
        c.pop('text')
        for url in ['data:image/jpeg;base64,/Users/test: error\n/9j/', 'data:image/png;base64,', 'data:image/png;base64,%GG']:
            c['url'] = url
            self.assertTrue(self.codes(self.card))
        for url in ['data:image/png;base64,YQ==', 'data:image/svg+xml,%3Csvg/%3E', 'dingtalk://some-media']:
            c['url'] = url
            self.assertFalse(self.codes(self.card))

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
