#!/usr/bin/env python3
"""Inspect complete template fixtures, render index reuse boundaries, and verify media provenance."""
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


def pointer_value(document, pointer):
    value = document
    if not pointer.startswith('/'):
        raise ValueError('Expected an absolute JSON Pointer')
    for part in pointer[1:].split('/'):
        key = part.replace('~1', '/').replace('~0', '~')
        if isinstance(value, list):
            if not key.isdecimal():
                raise ValueError('Expected a nonnegative array index')
            value = value[int(key)]
        else:
            value = value[key]
    return value


def component_definitions(messages):
    """Inspect initial fixture definitions from inline or separate messages.

    Return the original list for a single batch so derivation tests can edit it.
    Multiple batches are projected by component ID for read-only inspection.
    This is a test/media projection, not a production payload assembler.
    """
    batches = []
    for message in messages:
        for kind in ('createSurface', 'updateComponents'):
            if 'components' in message.get(kind, {}):
                batches.append(message[kind]['components'])
    if len(batches) == 1:
        return batches[0]
    definitions = {}
    for batch in batches:
        for component in batch:
            definitions[component['id']] = component
    return list(definitions.values())


def template_reuse_facts(messages, input_catalog=None):
    """Inspect fixture bindings and events, without evaluating client behavior.

    Fixed counts are indexed slots, not arbitrary array lengths. Repeated
    children include Column/Row templates as well as List/Loop templates.
    """
    def objects(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from objects(child)
        elif isinstance(value, list):
            for child in value:
                yield from objects(child)

    data = {}
    for message in messages:
        creation = message.get('createSurface', {})
        if 'dataModel' in creation:
            data = creation['dataModel']
        update = message.get('updateDataModel', {})
        if update.get('path', '/') == '/' and 'value' in update:
            data = update['value']
    components = component_definitions(messages)
    nodes = list(objects(components))
    paths = set()
    for node in nodes:
        if isinstance(node.get('path'), str) and node['path'].startswith('/'):
            paths.add(node['path'])
        if node.get('call') == 'formatString':
            value = node.get('args', {}).get('value', '')
            if isinstance(value, str):
                paths.update(re.findall(r'\$\{(/[^}]+)\}', value))

    fixed = {}
    for path in paths:
        value, prefix = data, []
        for token in path[1:].split('/'):
            key = token.replace('~1', '/').replace('~0', '~')
            if isinstance(value, list):
                if not key.isdecimal():
                    break
                collection = '/' + '/'.join(prefix)
                fixed[collection] = max(fixed.get(collection, 0), int(key) + 1)
                if int(key) >= len(value):
                    break
                value = value[int(key)]
            elif isinstance(value, dict) and key in value:
                value = value[key]
            else:
                break
            prefix.append(token)

    repeated = sorted({child['path'] for node in nodes for child in [node.get('children')]
                       if isinstance(child, dict) and isinstance(child.get('componentId'), str)
                       and isinstance(child.get('path'), str) and child['path'].startswith('/')})
    events = sorted({node['event']['name'] for node in nodes
                     if isinstance(node.get('event'), dict)
                     and isinstance(node['event'].get('name'), str)})
    if input_catalog is None:
        source = Path(__file__).resolve().parents[1] / 'skills/dingtalk-aicard/references/protocol/catalog-components-inputs.json'
        input_catalog = json.loads(source.read_text(encoding='utf-8'))['components']
    input_types = {name for name, definition in input_catalog.items()
                   if 'value' in definition.get('properties', {})}
    inputs = {component['value']['path'] for component in components
              if component['component'] in input_types
              and component.get('disabled') is not True
              and isinstance(component.get('value'), dict)
              and isinstance(component['value'].get('path'), str)}
    input_events = set()
    for component in components:
        if component['component'] not in {'Button', 'ButtonGroup'}:
            continue
        for node in objects(component):
            event = node.get('event', {})
            targets = [obj.get('path') for obj in objects(event.get('context', {}))]
            if any(isinstance(target, str) and any(
                    path == target or path.startswith(target.rstrip('/') + '/') for path in inputs)
                   for target in targets):
                input_events.add(event['name'])
    return {'fixed': dict(sorted(fixed.items())), 'repeated': repeated,
            'events': events, 'input_events': sorted(input_events)}


def format_reuse_boundary(facts):
    """Render reuse facts as the template index's Reuse boundary cell."""
    def names(values):
        return ', '.join(f'`{value}`' for value in values) or 'none'
    fixed = ', '.join(f'`{path}`={count}' for path, count in facts['fixed'].items()) or 'none'
    return (f"Fixed: {fixed}; Repeated: {names(facts['repeated'])}; "
            f"Events: {names(facts['events'])}; Input events: {names(facts['input_events'])}")


def image_urls(messages):
    """Resolve final image fields in complete initial fixture snapshots."""
    models, definitions = {}, {}
    for message in messages:
        create = message.get('createSurface')
        if create:
            sid = create['surfaceId']
            models[sid] = create.get('dataModel', {})
            definitions[sid] = {c['id']: c for c in create.get('components', [])}
        data = message.get('updateDataModel')
        if data and data.get('path', '/') == '/':
            models[data['surfaceId']] = data['value']
        update = message.get('updateComponents')
        if update:
            definitions.setdefault(update['surfaceId'], {}).update(
                {c['id']: c for c in update['components']})
    for sid, components in definitions.items():
        for component in components.values():
            if component['component'] != 'Image':
                continue
            for field in ('url', 'darkUrl'):
                if field not in component:
                    continue
                value = component[field]
                if isinstance(value, dict):
                    value = pointer_value(models[sid], value['path'])
                if not isinstance(value, str) or not value:
                    raise ValueError('Expected a resolved image URL')
                yield component['id'], field, value


def validate_media(payloads, upload_manifest, artwork_provenance):
    """Check actual URLs and every manifest reference; do not perform network I/O."""
    assets = upload_manifest['assets']
    native = artwork_provenance['nativeMarkdownFileArtwork']['assets']
    known = {asset['url'] for asset in assets + native}
    errors = []
    for slug, messages in payloads.items():
        try:
            for ident, field, url in image_urls(messages):
                parsed = urlsplit(url)
                if parsed.scheme != 'https' or not parsed.netloc or url not in known:
                    errors.append({'template': slug, 'component': ident, 'field': field,
                                   'code': 'unverified_image_url', 'url': url})
        except (KeyError, IndexError, TypeError, ValueError) as error:
            errors.append({'template': slug, 'code': 'image_binding_error', 'detail': str(error)})
    for asset in assets:
        for reference in asset['references']:
            try:
                actual = pointer_value(payloads[reference['template']], reference['path'])
                if actual != asset['url']:
                    raise ValueError('Manifest pointer does not match its asset URL')
            except (KeyError, IndexError, TypeError, ValueError) as error:
                errors.append({'code': 'asset_reference_error', 'reference': reference,
                               'detail': str(error)})
    return errors


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(
        description='Print the template index Reuse boundary cell for complete template files.')
    parser.add_argument('files', nargs='+', type=Path, help='Template a2ui.json files')
    args = parser.parse_args(argv)
    for path in args.files:
        messages = json.loads(path.read_text(encoding='utf-8'))
        print(f'{path.parent.name}: {format_reuse_boundary(template_reuse_facts(messages))}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
