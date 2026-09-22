#!/usr/bin/env python3
# Copyright 2026 Alibaba Group
# Licensed under the Apache License, Version 2.0.

"""Regress schema-engine equivalence, diagnostics, and performance."""
from __future__ import annotations
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
# Localized values cover identifier and diagnostic behavior under Unicode.
from unittest.mock import mock_open, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/dingtalk-aicard/scripts'))
import aicard_lint as lint


def message(node):
    return {'version': 'v1.0', 'updateComponents': {'surfaceId': 's', 'components': [node]}}


def text_node(**extra):
    return dict(id='root', component='Text', text='正文', **extra)


def nested_not(depth, value=True):
    for _ in range(depth):
        value = {'call': 'not', 'args': {'value': value}}
    return value


class SchemaEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol = lint.Protocol(str(ROOT / 'skills/dingtalk-aicard/references/protocol'))
        cls.reference = lint.SchemaEngine(cls.protocol.schema_engine.docs, optimize=False)

    def diagnostics(self, node):
        return lint.lint([message(node)], self.protocol)[0]

    def test_unicode_baseline_rejects_missing_or_modified_table(self):
        with patch.object(lint, 'UNICODE_TABLE', str(ROOT / 'missing-unicode-table.json')):
            with self.assertRaisesRegex(lint.ProtocolError, 'Bundled Unicode tables'):
                lint.SchemaEngine(self.protocol.schema_engine.docs)
        with patch.object(lint, 'UNICODE_TABLE_SHA256', '0' * 64):
            with self.assertRaisesRegex(lint.ProtocolError, 'Character-table digest mismatch'):
                lint.SchemaEngine(self.protocol.schema_engine.docs)
        requirements = (ROOT / 'skills/dingtalk-aicard/scripts/requirements.txt').read_text().splitlines()
        self.assertFalse(any(line.startswith('regex') for line in requirements))

    def test_standard_regex_uses_frozen_identifier_rules(self):
        engine = lint.ProtocolRegex()
        for name, valid in (('中文', True), ('_', True), ('1name', False),
                            ('a\u200c', True), ('\u088f', True), ('a\u1acf', True),
                            ('\u0378', False), ('a-b', False), ('', False), ('😀', False)):
            with self.subTest(name=name):
                self.assertEqual(valid, bool(engine.search(engine.identifier_pattern, name)))
        with self.assertRaises(lint.re.error):
            engine.compile(r'\p{Unsupported_Property}')

    def test_unicode_table_digest_ignores_file_format_but_rejects_changed_rules(self):
        raw = Path(lint.UNICODE_TABLE).read_bytes()
        table = json.loads(raw)
        variants = [raw.replace(b'\n', b'\r\n'), b'\xef\xbb\xbf' + raw,
                    json.dumps(table, sort_keys=True, separators=(',', ':')).encode('utf-8')]
        for content in variants:
            with self.subTest(content=content[:20]), patch('builtins.open', mock_open(read_data=content)):
                self.assertEqual(table['classes'], lint.ProtocolRegex().classes)
        for name in ('XID_Start', 'XID_Continue'):
            changed = copy.deepcopy(table)
            changed['classes'][name] += '0'
            with self.subTest(name=name), patch('builtins.open', mock_open(read_data=json.dumps(changed).encode())):
                with self.assertRaisesRegex(lint.ProtocolError, 'Character-table digest mismatch'):
                    lint.ProtocolRegex()
        for changed in ([], {'unicodeVersion': '16.0.0'},
                        dict(table, classes=[]), dict(table, classes={'XID_Start': 0, 'XID_Continue': ''})):
            with self.subTest(table=changed), patch('builtins.open', mock_open(read_data=json.dumps(changed).encode())):
                with self.assertRaises(lint.ProtocolError):
                    lint.ProtocolRegex()

    def test_lint_and_asset_generation_do_not_import_regex(self):
        import builtins
        original = builtins.__import__

        def without_regex(name, *args, **kwargs):
            if name == 'regex' or name.startswith('regex.'):
                raise ModuleNotFoundError('regex disabled by this test', name='regex')
            return original(name, *args, **kwargs)

        with patch.object(builtins, '__import__', side_effect=without_regex):
            protocol = lint.Protocol(str(ROOT / 'skills/dingtalk-aicard/references/protocol'))
            self.assertEqual([], lint.lint([message(text_node())], protocol)[0])
            import build_go_assets
            generated = build_go_assets.generate()
        self.assertEqual(protocol.schema_engine.regex.classes, generated['unicodeClasses'])

    def test_unicode_17_extension_names_match_go_baseline(self):
        # U+088F became an identifier start in Unicode 17; U+0378 remains unassigned.
        for name, valid in (('\u088f', True), ('a\u1acf', True), ('\u0378', False)):
            with self.subTest(name=name):
                value = [{'version': 'v1.0', 'createSurface': {
                    'surfaceId': 's', 'metadata': {'extensions': {name: {}}}}}]
                ds, _ = lint.lint(value, self.protocol)
                self.assertEqual(valid, not ds, ds)

    def test_unknown_fields_are_named_and_point_to_each_field(self):
        cases = [
            (text_node(bogus=1), '/bogus', 'bogus'),
            (text_node(**{'a/~b': 1}), '/a~1~0b', 'a/~b'),
            (text_node(visible={'call': 'not', 'args': {'value': True, 'bogus': 1}}),
             '/visible/args/bogus', 'bogus'),
        ]
        for node, suffix, field in cases:
            with self.subTest(field=field, suffix=suffix):
                ds = self.diagnostics(node)
                self.assertEqual(1, len(ds), ds)
                self.assertEqual('schema.unknown_property', ds[0]['code'])
                self.assertEqual('/0/updateComponents/components/0' + suffix, ds[0]['pointer'])
                self.assertIn(field, ds[0]['message'])

    def test_unknown_fields_do_not_hide_other_errors(self):
        node = text_node(bogus=1, other=2)
        node['text'] = 42
        ds = self.diagnostics(node)
        base = '/0/updateComponents/components/0'
        self.assertEqual({'/text', '/bogus', '/other'},
                         {d['pointer'].removeprefix(base) for d in ds})
        node = text_node(visible={'call': 'not', 'args': {'value': 'wrong', 'bogus': 1}})
        ds = self.diagnostics(node)
        self.assertEqual({'/visible/args/value', '/visible/args/bogus'},
                         {d['pointer'].removeprefix(base) for d in ds})

    def test_enum_and_unknown_function_have_no_duplicate_diagnostic(self):
        for field, value in (('colorToken', 'not-a-token'),
                             ('visible', {'call': 'notAFunction', 'args': {}})):
            with self.subTest(field=field):
                ds = self.diagnostics(text_node(**{field: value}))
                self.assertEqual(1, len(ds), ds)
                self.assertEqual('schema.bad_enum', ds[0]['code'])

    def test_cache_is_scoped_to_one_check_and_does_not_mutate_input(self):
        node = text_node()
        before = copy.deepcopy(node)
        self.assertEqual([], self.diagnostics(node))
        self.assertEqual(before, node)
        node['text'] = 42
        self.assertTrue(self.diagnostics(node))
        node['text'] = '恢复'
        self.assertEqual([], self.diagnostics(node))
        self.assertIsNone(self.protocol.schema_engine._valid_subtrees)

    def test_all_components_and_mutations_match_unoptimized_engine(self):
        for name in self.protocol.components:
            original = lint.explain(self.protocol, name)['example']
            cases = [original, dict(original, bogus=1), dict(original, id=[])]
            missing = dict(original)
            del missing['component']
            cases.append(missing)
            for index, node in enumerate(cases):
                with self.subTest(component=name, mutation=index):
                    value = message(node)
                    reference = self.reference.validator('agent-to-renderer.json').is_valid(value)
                    optimized = not self.protocol.schema_engine.check(value, 'agent-to-renderer.json')
                    self.assertEqual(reference, optimized)
                    self.assertEqual(index == 0, optimized)

    def test_composition_annotations_and_branch_failures_match_standard_engine(self):
        import jsonschema
        cases = [
            ({'allOf': [{'properties': {'a': {'type': 'string'}}},
                        {'properties': {'b': {'type': 'number'}}}],
              'unevaluatedProperties': False},
             [({'a': 'x', 'b': 2}, True), ({'a': 'x', 'extra': 1}, False), ({'a': 1}, False)]),
            ({'anyOf': [{'properties': {'a': {'type': 'string'}}, 'required': ['a']},
                        {'properties': {'b': {'type': 'number'}}, 'required': ['b']}],
              'unevaluatedProperties': False},
             [({'a': 'x', 'b': 2}, True), ({'a': 'x', 'b': 'bad'}, False), ({'b': 2}, True)]),
            ({'oneOf': [{'properties': {'x': {'type': 'string'}}, 'required': ['x']},
                        {'properties': {'x': {'const': 'same'}}, 'required': ['x']}],
              'unevaluatedProperties': False},
             [({'x': 'same'}, False), ({'x': 'different'}, True), ({'x': 1}, False)]),
            ({'properties': {'kind': {'type': 'string'}},
              'if': {'properties': {'kind': {'const': 'a'}}, 'required': ['kind']},
              'then': {'properties': {'a': {'type': 'string'}}},
              'else': {'properties': {'b': {'type': 'boolean'}}},
              'unevaluatedProperties': False},
             [({'kind': 'a', 'a': 'yes'}, True), ({'kind': 'a', 'b': True}, False),
              ({'kind': 'b', 'b': True}, True)]),
            ({'$defs': {'A': {'properties': {'a': {'type': 'string'}}}},
              '$ref': '#/$defs/A', 'properties': {'b': {'type': 'number'}},
              'unevaluatedProperties': False},
             [({'a': 'x', 'b': 2}, True), ({'a': 'x', 'extra': 1}, False)]),
            ({'anyOf': [{'properties': {'call': {'const': 'a'}, 'arg': {'type': 'string'}},
                        'required': ['call', 'arg']},
                       {'properties': {'call': {'const': 'b'}, 'arg': {'type': 'number'}},
                        'required': ['call', 'arg']}], 'unevaluatedProperties': False},
             [({'call': 'b', 'arg': 2}, True), ({'call': 'b', 'arg': 'bad'}, False),
              ({'call': 'a', 'arg': 'x', 'extra': 1}, False)]),
        ]
        for index, (spec, examples) in enumerate(cases):
            document = dict(spec, **{'$id': 'https://example.invalid/test.json',
                                    '$schema': 'https://json-schema.org/draft/2020-12/schema'})
            engine = lint.SchemaEngine({'test.json': document})
            for value, expected in examples:
                with self.subTest(schema=index, value=value):
                    self.assertEqual(expected, jsonschema.Draft202012Validator(document).is_valid(value))
                    self.assertEqual(expected, not engine.check(value, 'test.json'))

    def test_dynamic_scope_uses_unoptimized_validation(self):
        document = {
            '$id': 'https://example.invalid/node.json',
            '$schema': 'https://json-schema.org/draft/2020-12/schema',
            '$dynamicAnchor': 'node', 'type': 'object',
            'properties': {'children': {'type': 'array', 'items': {'$dynamicRef': '#node'}}},
        }
        engine = lint.SchemaEngine({'node.json': document})
        self.assertTrue(engine._has_dynamic_refs)
        self.assertFalse(engine.check({'children': [{}]}, 'node.json'))
        self.assertTrue(engine.check({'children': [1]}, 'node.json'))

    def test_unicode_patterns_keep_reference_semantics(self):
        for key in ('中文', '_a1', 'a\u200c', 'a\n', '\U000105c0', '🙂', 'a-b'):
            value = {'version': 'v1.0', 'createSurface': {
                'surfaceId': 's', 'metadata': {'extensions': {key: True}}}}
            with self.subTest(key=repr(key)):
                expected = self.reference.validator('agent-to-renderer.json').is_valid(value)
                self.assertEqual(expected, not self.protocol.schema_engine.check(value, 'agent-to-renderer.json'))

    def test_nested_expression_cli_finishes_within_test_budget(self):
        # A subprocess timeout bounds exponential cases without making local timing a protocol limit.
        conditions = {'call': 'and', 'args': {'values': [
            {'call': 'eq', 'args': {'a': {'path': '/status'}, 'b': 'todo'}},
            {'call': 'gt', 'args': {'a': {'path': '/count'}, 'b': 0}},
            {'call': 'not', 'args': {'value': {'call': 'eq', 'args': {'a': {'path': '/name'}, 'b': ''}}}},
        ]}}
        for expression, valid in ((nested_not(10), True), (nested_not(4, 'bad'), False), (conditions, True)):
            with self.subTest(valid=valid), tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR') or ROOT) as directory:
                path = Path(directory) / 'card.json'
                path.write_text(json.dumps([message(text_node(visible=expression))]))
                proc = subprocess.run([sys.executable, str(ROOT / 'skills/dingtalk-aicard/scripts/aicard_lint.py'),
                                       str(path), '--format', 'json'], capture_output=True, text=True, timeout=15)
                self.assertEqual(0 if valid else 1, proc.returncode, proc.stderr)
                self.assertEqual(valid, json.loads(proc.stdout)['valid'])

    def test_resource_exhaustion_is_reported_as_incomplete(self):
        engine = self.protocol.schema_engine
        with patch.object(engine, 'validator') as validator:
            validator.return_value.iter_errors.side_effect = RecursionError()
            with self.assertRaises(lint.ValidationLimitError):
                engine.check(message(text_node()), 'agent-to-renderer.json')
        self.assertIsNone(engine._valid_subtrees)
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR') or ROOT) as directory:
            path = Path(directory) / 'card.json'
            path.write_text(json.dumps([message(text_node())]))
            with patch.object(lint.SchemaEngine, 'check', side_effect=lint.ValidationLimitError('资源预算不足')), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                code = lint.main([str(path), '--format', 'json'])
            self.assertEqual(2, code)
            result = json.loads(output.getvalue())
            self.assertFalse(result['valid'])
            self.assertEqual('input.validation_incomplete', result['diagnostics'][0]['code'])


if __name__ == '__main__':
    unittest.main()
