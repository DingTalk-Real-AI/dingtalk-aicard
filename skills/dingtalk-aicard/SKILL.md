---
name: dingtalk-aicard
description: >
  Create, edit, and validate DingTalk AI Card (A2UI) JSON files offline.
  Use for cards built from requirements or images, changes to existing cards,
  structural protocol errors, and named component or function contract lookup.
metadata:
  version: "0.1.1"
  category: product
  requires:
    bins:
      - python3
  cliHelp: "python3 scripts/aicard_lint.py --help"
  aicardVersion: "V0.8"
  protocolVersion: "1.0"
  catalogId: https://dingtalk.com/card/a2ui/catalogs/public/catalog.json
---

# DingTalk AI Cards V0.8

Create or edit A2UI from user content or images; query needed contracts. Distinguish local validation, delivery, and rendering. DingTalk V0.8 uses A2UI 1.0: message `version` is `"v1.0"`; send API `protocolVersion` is `"1.0"`.

Follow the requested lookup, file, send, or update; references are not a mandatory sequence. Obey command and protocol requirements. Ask only if missing facts, interaction meaning, or target affect the outcome.

## Route by intent

Design supplies visual principles; patterns give reusable content and interaction strategies; templates are complete scene examples. Read only the relevant reference. Do not preload the complete protocol, every pattern, or the whole guide.

| Intent | Start with | Optional follow-up |
|---|---|---|
| Set up lookup and validation | [Execution environment](#execution-environment) | Installed command help |
| Reuse a bundled template | Its [reuse boundary](references/templates/README.md) and selected JSON | Components outside the data model; pattern for changed behavior |
| Create or redesign from requirements | [Visual focus and layout](references/design.md) | One [pattern](#composition-patterns); a template only if compatible |
| Reconstruct an image | [Image reconstruction](references/design.md#image-reconstruction) | Selected component contracts |
| Query a known name | [Query a named contract](#query-a-named-contract) | The result's source pointer |
| Find an unknown name | [Component](references/index/components.md), [function](references/index/functions.md), or [Token](references/index/tokens.md) index | Query only the selected names |
| Construct or repair a card, or prepare an update | Current messages and [Construct and validate](#construct-and-validate) | Affected guidance, diagnostic pointer, and named contract |
| Send, preview, or deliver an update | [Delivery boundary](#delivery-boundary) | Installed command help |

Use patterns for composition guidance and linked JSON for message shapes. Read a named capability example directly; use the [example index](references/protocol/examples/README.md) only to choose one. Template reuse must match structure, collection sizes, and interactions.

## Composition patterns

| Content | Reference |
|---|---|
| Notifications and reminders | [Short notifications](references/patterns/detail.md#short-notifications) |
| Object facts and decisions | [Detail](references/patterns/detail.md) |
| Illustrated content and attachments | [Content](references/patterns/content.md) |
| Metrics and reports | [Report](references/patterns/report.md) |
| Editable inputs and submission | [Form](references/patterns/form.md) |
| Generation and execution updates | [Progress](references/patterns/progress.md) |

For editable inputs, also consult the form pattern for bindings and submission,
even when another pattern describes the card.
When replaying an execution example, read [Examples and replay](references/patterns/progress.md#examples-and-replay) for its batch boundaries, even if the JSON was selected directly.

## Execution environment

<!-- aicard:runtime:start -->
This standalone edition uses Python 3.10+ for lookup and validation; DWS is optional for delivery. Use a healthy existing environment or prepare one from a writable task directory:

```bash
python3 /absolute/path/to/dingtalk-aicard/scripts/setup_env.py
```

Require exit code 0 and `ready: true`; use the returned absolute `pythonExecutable` for subsequent commands. Setup may use network access once; lookup and validation are offline. Use `--venv` or `--python` when needed. Run the lint script with `--self-check` to check the installed Python protocol package. Do not use `--user` or `--break-system-packages`. If setup fails, inspect `error.code` and `logPath`; reading references can still support a draft but is not a validation pass.
<!-- aicard:runtime:end -->

## Query a named contract

<!-- aicard:query:start -->
```bash
"<pythonExecutable>" /absolute/path/to/dingtalk-aicard/scripts/aicard_lint.py --explain Tabs --format json
"<pythonExecutable>" /absolute/path/to/dingtalk-aicard/scripts/aicard_lint.py --explain-many Text Row Column --compact --format json
```
<!-- aicard:query:end -->

Query only needed names. Compact `$contractRef` and `definitions` are lookup metadata, not A2UI fields. Query without `--compact` for expanded fields; `source` locates omitted details.

## Construct and validate

Use `references/protocol/` as the contract. Write an ordered JSON message array.
Within one card, keep the same `surfaceId`; choose an ID suitable for that card
when creating it. A complete new card requires Surface creation, root data
initialization (even `{}` for static content), and a component with ID `root`.
The recommended explicit form is:

```json
[
  {"version":"v1.0","createSurface":{"surfaceId":"example-surface","catalogId":"https://dingtalk.com/card/a2ui/catalogs/public/catalog.json"}},
  {"version":"v1.0","updateDataModel":{"surfaceId":"example-surface","path":"/","value":{}}},
  {"version":"v1.0","updateComponents":{"surfaceId":"example-surface","components":[{"id":"root","component":"Text","text":"Example"}]}}
]
```

This is a structural example, not a business card to send. Protocol-valid inline
initialization in `createSurface.dataModel` and `createSurface.components` is also
accepted; three separate messages are not the only valid representation.

| Scenario | Messages and checks |
|---|---|
| Complete new card | Create and initialize as above; use `--preflight new-card` |
| Host-created empty Surface | Initialize its existing ID without `createSurface`; use Schema and resource checks |
| Existing card update | Keep IDs and send only intentional changes; use Schema and resource checks, then check against known current state |

Removing `createSurface` from a complete file does not make it a safe delta.
Do not replay form defaults or unrelated initial data: that can overwrite user
input. Resource checks alone cannot prove an update preserves state or references.

Preserve unrelated styling when editing. When reusing a template, keep its frame and insets; the new-card defaults below do not override template styling or explicit surface designs. For ordinary new cards, prefer a root `Column` without `backgroundColor` or `backgroundColorToken`. If a root `Card` is needed, use `backgroundColor: "#00FFFFFF"` and omit `backgroundColorToken`: omitting both fields gives Card an opaque default, and a configured background Token takes precedence over the color. See [design guidance](references/design.md#root-surface).

Prefer `Markdown` for rich text and static tables; use `Text` for short titles and labels. See [report composition](references/patterns/report.md) for table selection and pagination. Omit root `padding` for ordinary new cards; omission is not equivalent to explicit `0`. Do not default to `CardHeader`; preserve it when requested and check [insets](references/design.md#headers-and-card-insets). These are authoring defaults, not Schema restrictions.

Preserve supplied facts and requested interactions; label demonstration data. Replace `REPLACE_*` placeholders before delivery. Do not present unverified resources, unconnected actions, or simulated states as real and working.

Choose literals or bindings as the field contract permits. Initialize bound data with the required types and update it through `updateDataModel`; change component literals and layout through `updateComponents`. `sendDataModel` controls whether A2A messages include the full data model, not whether bindings work.

<!-- aicard:lint:start -->
```bash
"<pythonExecutable>" /absolute/path/to/dingtalk-aicard/scripts/aicard_lint.py a2ui.json --format json
```

Choose `--preflight new-card` for a complete creation file or `--preflight resources` for media resources in a host-created initialization or delta. Exit code 0 means the selected checks passed, 1 means validation failed, and 2 means an input, environment or usage error. Check `valid` and selected `preflight.valid`; a local check never proves client rendering. Fix errors at their diagnostic pointers and rerun the selected check.
<!-- aicard:lint:end -->

## Validation scope

Lint checks syntax and Schema. New-card preflight checks initialization, root, IDs, final-snapshot static references, cycles, and ownership. Multiple parents, repeated child slots, shared template sources, and static/template overlap fail even with empty arrays; unreachable nodes warn. Use distinct IDs per placement. Bindings, dynamic expansion, intermediate frames, and rendering remain unverified. Resource preflight rejects literal `data:` resource fields (`resource.inline_data_unsupported`); Markdown/`fallbackMarkdown` markers, even code/comments, only warn (`resource.markdown_data_unverified`) for review. This does not parse Markdown/HTML, evaluate bindings, or fetch resources; a clean report proves neither absence of inline media nor loading. Prefer HTTPS [assets](references/design.md#media-resources). `--fragment` never bypasses initialization. Do not silently remove requested data, components, or interactions.

Use self-check for package integrity, not named lookup. Review facts, actions, visual completeness, and first-frame values. Verify loading, functions, rendering, interaction, and writeback on the client; report delivery evidence separately.

## Delivery boundary

<!-- aicard:delivery:start -->
This standalone Skill produces and validates files; it does not require DWS.
Send only when requested through an available delivery capability, such as the
DWS chat Skill. Hand off the file, whether it is a creation or delta, checks run,
and unresolved issues. Follow that capability's current target, receipt and
completion contract. Without a delivery capability, report sending as not run.
After validation, if the receiving tool requires a JSON array of message strings,
serialize each object, then encode the array. Python lint does not emit that wire
format. Run this in the directory containing the validated `a2ui.json`:

```python
import json
from pathlib import Path
messages = json.loads(Path("a2ui.json").read_text(encoding="utf-8"))
print(json.dumps([json.dumps(message, ensure_ascii=False) for message in messages], ensure_ascii=False))
```
<!-- aicard:delivery:end -->

Report the file, checks actually run, and delivery result when requested. Local validation and request acceptance do not prove client rendering.
