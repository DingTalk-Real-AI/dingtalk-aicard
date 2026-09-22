#!/usr/bin/env python3
# Copyright 2026 Alibaba Group
# Licensed under the Apache License, Version 2.0.

"""Regress protocol checks, false positives, diagnostic locations, and CLI failures."""
from __future__ import annotations
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills/dingtalk-aicard/scripts/aicard_lint.py'
spec = importlib.util.spec_from_file_location('aicard_lint', SCRIPT)
lint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lint)


def component(**changes):
    return dict({'id': 'root', 'component': 'Text', 'text': '校验正文'}, **changes)


def message(components=None, surface='test'):
    return {'version': 'v1.0', 'updateComponents': {
        'surfaceId': surface, 'components': components if components is not None else [component()]}}


class ValidatorTests(unittest.TestCase):
    def test_nested_explain_preserves_open_string_guidance_and_source(self):
        result = lint.explain(self.protocol, 'Chart')
        data = next(f for f in result['fields'] if f['name'] == 'data')
        shape = data['shape']['anyOf'][1]['properties']
        self.assertIn('lineChart', shape['type']['description'])
        self.assertNotIn('enum', shape['type'])
        deferred = shape['data']['items']['properties']['x']
        self.assertTrue(deferred['detailDeferred'])
        shard, pointer = deferred['source'].split('#', 1)
        node = self.protocol.docs[shard]
        for raw in pointer.strip('/').split('/'):
            key = raw.replace('~1', '/').replace('~0', '~')
            node = node[int(key)] if isinstance(node, list) else node[key]
        self.assertIn('anyOf', node)
        self.assertIn('lineChart', lint.render_explain(result))

    def test_static_buttons_follow_public_contract(self):
        self.assertTrue(self.errors({'id': 'root', 'component': 'Button',
                                     'child': 'label', 'disabled': True}, fragment=True))
        self.assertFalse(self.errors({'id': 'root', 'component': 'ButtonGroup',
                                      'buttons': [{'label': '关闭', 'variant': 'default'},
                                                  {'label': '提交', 'variant': 'primary'}]}, fragment=True))

    @classmethod
    def setUpClass(cls):
        cls.protocol = lint.Protocol(ROOT / 'skills/dingtalk-aicard/references/protocol')

    def diagnostics(self, payload, fragment=False):
        return lint.lint(payload, self.protocol, fragment)[0]

    def errors(self, payload, fragment=False):
        return [d for d in self.diagnostics(payload, fragment) if d['severity'] == 'error']

    def cli(self, value=None, *, raw=None, options=()):
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR') or ROOT) as temp:
            path = Path(temp) / "含 空格 ' $() 卡片.json"
            path.write_text(raw if raw is not None else json.dumps(value), encoding='utf-8')
            p = subprocess.run([sys.executable, str(SCRIPT), str(path), '--format', 'json', *options],
                               text=True, capture_output=True, timeout=15)
            self.assertNotIn('Traceback', p.stderr)
            return p.returncode, json.loads(p.stdout)

    def test_ordinary_text_is_not_placeholder(self):
        self.assertEqual([], self.errors([message([component(text='TODO FIXME xxx 占位 PLACEHOLDER <你的>')])]))

    def test_cli_accepts_only_a_leading_utf8_bom(self):
        payload = json.dumps([message()], ensure_ascii=False)
        code, result = self.cli(raw='\ufeff' + payload)
        self.assertEqual(0, code)
        self.assertTrue(result['valid'])
        code, result = self.cli(raw=' ' + '\ufeff' + payload)
        self.assertEqual(2, code)
        self.assertEqual('input.invalid_json', result['diagnostics'][0]['code'])

    def test_placeholder_text_is_not_a_structure_error(self):
        self.assertEqual([], self.diagnostics([message([component(text='REPLACE_WITH_TEXT')])]))

    def test_dependency_and_interpreter_failures_are_not_protocol_errors(self):
        import builtins
        import contextlib
        import io
        original_import = builtins.__import__
        for error, code in ((ModuleNotFoundError("缺少模块", name="jsonschema"), "input.dependencies_unavailable"),
                            (ImportError("动态库加载失败"), "input.python_environment_unavailable")):
            def import_module(name, *args, **kwargs):
                if name == 'jsonschema':
                    raise error
                return original_import(name, *args, **kwargs)
            with self.subTest(code=code), patch('builtins.__import__', side_effect=import_module), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(2, lint.main(['--self-check', '--format', 'json']))
            report = json.loads(output.getvalue())
            self.assertEqual(code, report['diagnostics'][0]['code'])
            self.assertIn('setup_env.py', report['diagnostics'][0]['message'])
            self.assertFalse(report['renderingVerified'])

    def test_static_fields_reject_bindings(self):
        for field in ('bold', 'maxLine'):
            with self.subTest(field=field):
                ds = self.errors([message([component(**{field: {'path': '/x'}})])])
                self.assertTrue(ds)
                self.assertIn(f'/0/updateComponents/components/0/{field}', [d['pointer'] for d in ds])
                self.assertTrue(all(d.get('keyword') for d in ds))

    def test_unhashable_identifiers_are_diagnostics(self):
        for field in ('id', 'component'):
            for value in ([], {}):
                with self.subTest(field=field, value=value):
                    self.assertTrue(self.errors([message([component(**{field: value})])]))
        for value in ([], {}):
            self.assertTrue(self.errors([message(surface=value)]))

    def test_removed_fields_rejected(self):
        for name, field, value in (('Text', 'color', '#fff'), ('Icon', 'color', '#fff'),
                                   ('ChoicePicker', 'filterable', True), ('NumberInput', 'step', 1),
                                   ('ProgressBar', 'color', 'red')):
            with self.subTest(name=name):
                value_component = lint.explain(self.protocol, name)['example']
                value_component[field] = value
                ds = self.errors(value_component, fragment=True)
                self.assertIn('schema.unknown_property', [d['code'] for d in ds])

    def test_fragment_forms_and_source_pointers(self):
        for payload, pointer in ((component(bold={}), '/bold'),
                                 ([component(bold={})], '/0/bold'),
                                 (message([component(bold={})]), '/updateComponents/components/0/bold')):
            with self.subTest(pointer=pointer):
                self.assertIn(pointer, [d['pointer'] for d in self.errors(payload, True)])
        self.assertEqual([], self.errors(component(id='standalone'), True))
        self.assertEqual([], self.errors({'id': 'a', 'component': 'Column', 'children': ['external']}, True))

    def test_message_identity_and_update_order_are_not_evaluated(self):
        for payload in ([message(), message([component(text='新版')])],
                        [message([component(), component()])],
                        [message(surface='a'), message(surface='b')]):
            self.assertEqual([], self.diagnostics(payload))

    def test_external_references_are_not_resolved(self):
        payload = [message([{'id': 'root', 'component': 'Column', 'children': ['a/~']},
                            {'id': 'a/~', 'component': 'Column', 'children': ['missing']}])]
        self.assertEqual([], self.diagnostics(payload))

    def test_component_graph_is_not_a_structure_constraint(self):
        cycles = [{'id': 'a', 'component': 'Column', 'children': ['b']},
                  {'id': 'b', 'component': 'Column', 'children': ['a']}]
        for payload, fragment in ((cycles, True), ([message([component(), *cycles])], False)):
            self.assertEqual([], self.diagnostics(payload, fragment))

    def test_long_component_chain_does_not_recurse(self):
        nodes = [{'id': 'root' if i == 0 else str(i), 'component': 'Column', 'children': [str(i + 1)]}
                 for i in range(1100)] + [component(id='1100')]
        ds, metrics = lint.lint([message(nodes)], self.protocol)
        self.assertFalse([d for d in ds if d['severity'] == 'error'])
        self.assertEqual({}, metrics)

    def test_data_updates_are_not_applied(self):
        payload = [message(), {'version': 'v1.0', 'updateDataModel': {'surfaceId': 'test', 'value': {'a': 1}}},
                   {'version': 'v1.0', 'updateDataModel': {'surfaceId': 'test', 'path': '/a/b', 'value': 2}}]
        before = copy.deepcopy(payload)
        self.assertEqual([], self.diagnostics(payload))
        self.assertEqual(before, payload)

    def test_metadata_is_not_function_or_binding(self):
        comp = component(metadata={'extensions': {'business': {'call': 'unknown', 'path': 'a~bad'}, '123-data': True}})
        self.assertEqual([], self.errors([message([comp])]))

    def test_design_and_host_choices_produce_no_diagnostics(self):
        nodes = [{'id': 'root', 'component': 'Row', 'align': 'stretch', 'children': ['title', 'image']},
                 component(id='title', text={'path': '/later'}, sizeToken='body'),
                 {'id': 'image', 'component': 'Image', 'url': 'file:///asset.png'}]
        self.assertEqual([], self.diagnostics([message(nodes)]))
        code, result = self.cli([message(nodes)], options=('--strict',))
        self.assertEqual(0, code)  # Keep the legacy flag without restoring design gates.
        self.assertTrue(result['valid'])
        self.assertEqual([], result['diagnostics'])
        self.assertIs(result['renderingVerified'], False)

    def test_non_finite_json_is_rejected_without_arbitrary_depth_policy(self):
        self.assertEqual('input.non_finite_number', self.errors([message([component(maxLine=float('nan'))])])[0]['code'])
        nested = {}
        for _ in range(55): nested = {'x': nested}
        self.assertEqual([], self.diagnostics([message([component(metadata={'extensions': {'business': nested}})])]))
        for raw in ('{', '[NaN]', '[]'):
            code, result = self.cli(raw=raw)
            self.assertIn(code, (1, 2))
            self.assertFalse(result['valid'])

    def test_missing_schema_and_external_ref_fail_closed(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR') or ROOT) as temp:
            code, result = self.cli([message()], options=('--protocol-dir', temp))
            self.assertEqual(2, code)
            self.assertEqual('input.protocol_unavailable', result['diagnostics'][0]['code'])
        docs = copy.deepcopy(self.protocol.docs)
        docs['agent-to-renderer.json']['$defs']['External'] = {'$ref': 'https://invalid.example/no-network.json'}
        with self.assertRaises(lint.ProtocolError):
            lint.SchemaEngine(docs)

    def test_deep_json_has_structured_failure(self):
        code, result = self.cli(raw='[' * 1200 + ']' * 1200)
        self.assertIn(code, (1, 2))
        self.assertIn(result['diagnostics'][0]['code'], ('schema.type_mismatch', 'input.invalid_json'))

    def test_unicode_surface_extensions(self):
        for key, valid in (('中文标识', True), ('_业务1', True), ('1业务', False), ('a-b', False)):
            with self.subTest(key=key):
                payload = {'version': 'v1.0', 'createSurface': {'surfaceId': 'test',
                           'metadata': {'extensions': {key: {'call': 'data'}}}}}
                self.assertEqual(valid, not bool(self.errors(payload, True)))

    def test_manifest_includes_envelope(self):
        self.assertEqual(set(lint.SHARDS) | {"a2ui-validation-rules.json"}, set(self.protocol.manifest()))
        self.protocol.schema_engine.self_check()

    def test_public_types_stay_public_and_internal_rules_remain_effective(self):
        private = {"ValueFunctionCall", "ActionFunctionCall", "EventAction", "ComponentMetadata"}
        public = {name for doc in self.protocol.docs.values() for name in doc.get("$defs", {})}
        self.assertFalse(private & public)
        self.assertTrue(private <= {name for doc in self.protocol.schema_engine.docs.values() for name in doc.get("$defs", {})})
        cases = [
            {"id": "root", "component": "TextField", "label": "输入", "checks": [{"condition": True, "message": "必填"}]},
            {"id": "root", "component": "Text", "text": {"call": "copyText", "catalogId": lint.HOST_CATALOG_ID, "args": {"text": "正文"}}},
            {"id": "root", "component": "Button", "child": "label", "action": {"functionCall": {"call": "add", "args": {"a": 1, "b": 2}}}},
            {"id": "root", "component": "CheckBox", "label": "同意", "value": False,
             "action": {"functionCall": {"call": "copyText", "catalogId": lint.HOST_CATALOG_ID, "args": {"text": "正文"}}}},
            {"id": "root", "component": "Button", "child": "label",
             "action": {"functionCall": {"call": "copyText", "catalogId": lint.HOST_CATALOG_ID, "args": {"text": "正文"}}},
             "metadata": {"extensions": {"dt_actionBindingsV1": {"action": {"resultPath": "relative"}}}}},
        ]
        for value in cases:
            with self.subTest(component=value):
                self.assertTrue(self.errors(value, True))
        for field in ("theme", "surfaceProperties"):
            self.assertTrue(self.errors({"version": "v1.0", "createSurface": {"surfaceId": "s", field: {}}}, True))
        self.assertEqual([], self.errors({"version": "v1.0", "createSurface": {"surfaceId": "s"}}, True))

    def test_rules_missing_and_mismatched_fail_closed(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR') or ROOT) as temp:
            code, result = self.cli([message()], options=("--validation-rules", str(Path(temp) / "missing.json")))
            self.assertEqual(2, code)
            self.assertEqual('input.protocol_unavailable', result['diagnostics'][0]['code'])
        for field, value in (("publicRef", "wrong.json#/$defs/Action"), ("path", ["missing"])):
            rules = copy.deepcopy(self.protocol.validation_rules)
            rules["references"][0][field] = value
            with self.assertRaises(lint.ProtocolError):
                lint.apply_validation_rules(self.protocol.docs, rules)
        with self.assertRaises(lint.ProtocolError):
            lint.apply_validation_rules(self.protocol.docs, {"version": 1, "definitions": {}, "references": []})
        before = copy.deepcopy(self.protocol.docs)
        lint.apply_validation_rules(self.protocol.docs, self.protocol.validation_rules)
        self.assertEqual(before, self.protocol.docs)

    def test_explain_host_results_and_visual_types(self):
        self.assertEqual(7, len(self.protocol.host_actions))
        for name in self.protocol.host_actions:
            result = lint.explain(self.protocol, name)
            self.assertIsInstance(result["resultSchema"], dict)
            self.assertIn('Result shape', lint.render_explain(result))
            self.assertNotIn('HostFunctionResults', result['description'])
        result = lint.explain(self.protocol, 'promptText')['resultSchema']
        self.assertIn('text', result['oneOf'][0]['properties']['data']['properties'])
        self.assertEqual(57, lint.explain(self.protocol, 'ColorToken')['count'])
        self.assertEqual(24, lint.explain(self.protocol, 'IconName')['count'])
        self.assertNotIn('checks', [f['name'] for f in lint.explain(self.protocol, 'TextField')['fields']])
        self.assertEqual('common-types-basic.json', lint.explain(self.protocol, 'Surface')['shard'])

    def test_cli_unhashable_value_returns_json(self):
        code, result = self.cli([message([component(id=[])])])
        self.assertEqual(1, code)
        self.assertFalse(result['valid'])
        self.assertTrue(result['diagnostics'])

    def test_delete_and_data_only_messages_need_no_renderable_tree(self):
        for op, body in (('deleteSurface', {'surfaceId': 'test'}),
                         ('updateDataModel', {'surfaceId': 'test', 'value': {'a': 1}})):
            self.assertEqual([], self.errors({'version': 'v1.0', op: body}, True))
            self.assertEqual([], self.errors([{'version': 'v1.0', op: body}]))
        self.assertEqual([], self.errors([message(), {'version': 'v1.0', 'deleteSurface': {'surfaceId': 'test'}}]))

    def with_data(self, nodes, data):
        return [message(nodes), {'version': 'v1.0', 'updateDataModel': {'surfaceId': 'test', 'value': data}}]

    def test_bound_values_do_not_participate_in_structure_checks(self):
        cases = [
            ({'id': 'root', 'component': 'Slider', 'min': 0, 'max': 100, 'value': {'path': '/v'}}, ['12', True, None, {}], [12, 0]),
            (component(text={'path': '/v'}), [12, False, {}], ['正文', '']),
            ({'id': 'root', 'component': 'Column', 'children': [], 'visible': {'path': '/v'}}, ['false', 0], [False, True]),
        ]
        for node, bad, good in cases:
            for value in bad + good:
                with self.subTest(component=node['component'], value=value):
                    ds = self.errors(self.with_data([node], {'v': value}))
                    self.assertEqual([], ds)

    def test_bound_objects_and_numbers_are_not_resolved(self):
        table = {'id': 'root', 'component': 'Table', 'data': {'path': '/table'}}
        self.assertFalse(self.errors(self.with_data([table], {'table': {'meta': [], 'list': []}})))
        carousel = {'id': 'root', 'component': 'ImageCarousel', 'items': [{'url': 'https://example.com/a.png'}], 'cornerRadius': {'path': '/radius'}}
        self.assertFalse(self.errors(self.with_data([carousel], {'radius': '12'})))
        self.assertFalse(self.errors(self.with_data([carousel], {'radius': 12})))

    def test_function_result_types_are_not_executed_or_inferred(self):
        number = {'id': 'root', 'component': 'Slider', 'min': 0, 'max': 100,
                  'value': {'call': 'formatNumber', 'args': {'value': 12}}}
        self.assertEqual([], self.diagnostics([message([number])]))
        number['value'] = {'call': 'add', 'args': {'a': {'call': 'formatNumber', 'args': {'value': 12}}, 'b': 1}}
        self.assertEqual([], self.diagnostics([message([number])]))
        number['value'] = {'call': 'add', 'args': {'a': {'path': '/a'}, 'b': 1}}
        for value in (2, 'bad'):
            self.assertEqual([], self.diagnostics(self.with_data([number], {'a': value})))
        number['value'] = {'call': 'add', 'args': {'a': 'bad', 'b': 1}}
        self.assertTrue(self.errors([message([number])]))  # Schema still rejects an invalid literal argument type.

    def test_template_execution_context_is_not_inferred(self):
        node = {'id': 'root', 'component': 'Slider', 'min': 0, 'max': 100, 'value': {'call': '@index'}}
        self.assertEqual([], self.diagnostics([message([node])]))
        self.assertEqual([], self.diagnostics(node, True))

    def test_template_data_is_not_expanded(self):
        nodes = [{'id': 'root', 'component': 'Column', 'children': {'componentId': 'item', 'path': '/rows'}},
                 {'id': 'item', 'component': 'Slider', 'min': 0, 'max': 100, 'value': {'path': 'n'}}]
        for value in (3, 'bad', {'n': 1}, None, [{'n': 2}, {'n': 'bad'}], []):
            self.assertEqual([], self.diagnostics(self.with_data(nodes, {'rows': value})))

    def test_nested_template_models_do_not_change_structure_validity(self):
        nodes = [{'id': 'root', 'component': 'Column', 'children': {'componentId': 'group', 'path': '/groups'}},
                 {'id': 'group', 'component': 'Column', 'children': {'componentId': 'item', 'path': 'rows'}},
                 component(id='item', text={'path': 'name'})]
        for name in ('正常', 8):
            model = {'groups': [{'rows': [{'name': name}]}]}
            self.assertEqual([], self.diagnostics(self.with_data(nodes, model)))

    def test_initial_and_late_values_are_not_evaluated(self):
        payload = self.with_data([component(text={'path': '/a~1b/~0'})], {'a/b': {'~': 123}})
        self.assertEqual([], self.diagnostics(payload))
        payload.append({'version': 'v1.0', 'updateDataModel': {'surfaceId': 'test', 'path': '/a~1b/~0', 'value': '已修正'}})
        self.assertEqual([], self.diagnostics(payload))
        self.assertEqual([], self.diagnostics(self.with_data([component(text={'path': '/later'})], {})))

    def test_literal_objects_are_validated_only_by_declared_schema(self):
        node = component(text={'call': 'objectGet', 'args': {'key': 'nested', 'value': {'nested': {'call': '@index', 'path': 'bad~x'}}}})
        # Structural acceptance does not establish runtime evaluation of nested content.
        self.assertFalse(self.errors([message([node])]))


    def test_large_template_data_is_opaque_to_structure_validation(self):
        nodes = [{'id': 'root', 'component': 'Column', 'children': {'componentId': 'item', 'path': '/rows'}},
                 component(id='item', text={'path': 'name'})]
        self.assertEqual([], self.diagnostics(self.with_data(nodes, {'rows': [{'name': 42} for _ in range(20)]})))


class ExplanationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol = lint.Protocol(ROOT / 'skills/dingtalk-aicard/references/protocol')

    def test_nested_child_location_is_available_without_catalog_read(self):
        result = lint.explain(self.protocol, 'Tabs')
        tabs = next(f for f in result['fields'] if f['name'] == 'tabs')
        self.assertIn('child', tabs['shape']['items']['properties'])
        self.assertIn('child', tabs['shape']['items']['required'])

    def test_event_only_slot_does_not_advertise_function_call(self):
        result = lint.explain(self.protocol, 'CheckBox')
        self.assertIn('action', result['eventOnlySlots'])
        action = next(f for f in result['fields'] if f['name'] == 'action')
        self.assertEqual(['event'], action['allowedActions'])
        self.assertNotIn('functionCall', json.dumps(action['shape']))
        self.assertNotIn('hostWriteback', result)

    def test_host_result_writeback_and_function_usage_are_visible(self):
        result = lint.explain(self.protocol, 'Button')
        self.assertIn('resultPath', result['hostWriteback']['binding']['properties'])
        self.assertEqual('action.functionCall', lint.explain(self.protocol, 'copyText')['usage'])
        self.assertEqual('value expression', lint.explain(self.protocol, 'not')['usage'])
        self.assertEqual(['onLinkClick'], lint.explain(self.protocol, 'Text')['hostWriteback']['slots'])
        self.assertEqual(['action'], lint.explain(self.protocol, 'Column')['hostWriteback']['slots'])
        self.assertNotIn('hostWriteback', lint.explain(self.protocol, 'Divider'))

    def test_button_group_writeback_belongs_to_inline_button(self):
        result = lint.explain(self.protocol, 'ButtonGroup')['hostWriteback']
        self.assertEqual('buttons[].metadata.extensions.dt_actionBindingsV1.action.resultPath', result['path'])
        self.assertEqual(['action'], result['slots'])

    def test_nested_event_only_slot_does_not_advertise_function_call(self):
        result = lint.explain(self.protocol, 'Tabs')
        tabs = next(f for f in result['fields'] if f['name'] == 'tabs')
        action = tabs['shape']['items']['properties']['action']
        self.assertEqual(['event'], action['allowedActions'])
        self.assertNotIn('functionCall', json.dumps(action))
        self.assertEqual(1, len(action['oneOf']))

    def test_common_action_expands_event_fields_without_all_functions(self):
        result = lint.explain(self.protocol, 'Action')
        branches = result['shape']['oneOf']
        event = next(b['properties']['event'] for b in branches if 'event' in b.get('properties', {}))
        self.assertIn('context', event['properties'])
        self.assertIn('name', event['required'])
        self.assertNotIn('showModal', json.dumps(result['shape']))


if __name__ == '__main__':
    unittest.main()
