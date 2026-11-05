const object = value => !!value && typeof value === 'object' && !Array.isArray(value);

function decode(value) {
  if (typeof value !== 'string') return object(value) ? value : {};
  if (value.length > 32768) throw new Error('too_large');
  const parsed = JSON.parse(value);
  return object(parsed) ? parsed : {};
}

/** Parse one untrusted A2UI callback shape; never use callback fields as identity. */
export function parseCallback(raw) {
  const data = decode(raw);
  if (JSON.stringify(data).length > 32768) throw new Error('too_large');
  const content = decode(data.content);
  const candidates = [];
  for (const source of [data, content]) {
    for (const path of [['actionData','a2uiAction'], ['a2uiAction'], ['action']]) {
      let node = source;
      for (const key of path) node = object(node) ? node[key] : undefined;
      if (object(node?.context)) candidates.push({ name: node.name ?? data.actionId, context: node.context });
    }
    for (const key of ['context','params']) {
      if (object(source[key])) candidates.push({ name: data.actionId ?? source[key].action, context: source[key] });
    }
  }
  if (!candidates.length) return { data };
  const unique = new Map(candidates.map(candidate => [JSON.stringify([candidate.name, candidate.context]), candidate]));
  if (unique.size !== 1) throw new Error('ambiguous_event');
  const event = [...unique.values()][0];
  if (data.actionId !== undefined && data.actionId !== event.name) throw new Error('conflicting_action');
  return { data, event };
}
