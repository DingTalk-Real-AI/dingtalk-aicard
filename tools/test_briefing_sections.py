#!/usr/bin/env python3
"""Verify briefing section content, data bindings, and displayed counts."""
import copy
import json
from pathlib import Path
import unittest

BASE = Path(__file__).resolve().parents[1] / 'skills/dingtalk-aicard/references/templates'


def resolve(value, data):
    if not isinstance(value, dict):
        return value
    if 'path' in value:
        result = data
        for part in value['path'].strip('/').split('/'):
            result = result[int(part)] if isinstance(result, list) else result[part]
        return result
    args = value['args']
    if value['call'] == 'eq':
        return resolve(args['a'], data) == resolve(args['b'], data)
    if value['call'] == 'or':
        return any(resolve(item, data) for item in args['values'])
    raise AssertionError(f"Unsupported expression: {value}")


def descendants(components, root):
    result = [components[root]]
    for child in components[root].get('children', []):
        result.extend(descendants(components, child))
    return result


class BriefingSectionTest(unittest.TestCase):
    def test_each_selected_section_exposes_bound_rows_and_consistent_count(self):
        for name in ('daily-agenda-briefing', 'daily-approval-briefing'):
            messages = json.loads((BASE / name / 'a2ui.json').read_text(encoding='utf-8'))
            components = {item['id']: item for item in messages[0]['createSurface']['components']}
            initial = messages[1]['updateDataModel']['value']
            sections = [('approval', 'approvals', '审批'), ('todo', 'todos', '待办')]
            if name == 'daily-agenda-briefing':
                sections.insert(0, ('agenda', 'agenda', '日程'))
            self.assertEqual({key for key, _, _ in sections}, set(initial['counts']))
            for section, collection, label in sections:
                with self.subTest(template=name, section=section):
                    data = copy.deepcopy(initial)
                    data['view'].update(selectedSection=section, layoutMode='tabs')
                    self.assertEqual(f'{label} {len(data[collection])}', data['counts'][section])
                    self.assertTrue(data[collection])
                    selected = components[f'{section}-section']
                    tabs = components.get('section-tabs')
                    if tabs:
                        self.assertEqual('Tabs', tabs['component'])
                        self.assertNotIn('visible', selected)
                        tab = next(item for item in tabs['tabs'] if item['child'] == selected['id'])
                        self.assertEqual(data['counts'][section], resolve(tab['title'], data))
                    else:
                        self.assertTrue(resolve(selected['visible'], data))
                        for other in ('agenda', 'approval', 'todo'):
                            if other != section:
                                self.assertFalse(resolve(components[f'{other}-section']['visible'], data))
                    visible_tree = descendants(components, selected['id'])
                    for index, row in enumerate(data[collection]):
                        binding = f'/{collection}/{index}/title'
                        titles = [item for item in visible_tree if item.get('text') == {'path': binding}]
                        self.assertEqual(1, len(titles))
                        self.assertEqual(row['title'], resolve(titles[0]['text'], data))
                    event = (tab if tabs else components[f'{section}-chip'])['action']['event']
                    self.assertEqual('daily_briefing_select_section', event['name'])
                    self.assertEqual(section, event['context']['section'])
                    self.assertEqual(data['identity']['businessId'], resolve(event['context']['businessId'], data))
                    self.assertEqual(data['briefing']['id'], resolve(event['context']['briefingId'], data))

    def test_todo_content_follows_data_updates_without_replacing_components(self):
        for name in ('daily-agenda-briefing', 'daily-approval-briefing'):
            with self.subTest(template=name):
                messages = json.loads((BASE / name / 'a2ui.json').read_text(encoding='utf-8'))
                components = {item['id']: item for item in messages[0]['createSurface']['components']}
                data = messages[1]['updateDataModel']['value']
                for index in range(3):
                    for field in ('title', 'details', 'statusText'):
                        data['todos'][index][field] = f'Updated {index} {field}'
                        component_field = {'title': 'title', 'details': 'details', 'statusText': 'status'}[field]
                        self.assertEqual(data['todos'][index][field], resolve(
                            components[f'todo-{component_field}__{index}']['text'], data))


if __name__ == '__main__':
    unittest.main()
