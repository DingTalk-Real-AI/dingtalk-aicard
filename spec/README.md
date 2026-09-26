---
status: authoritative
updated: 2026-09-21
scope: Files, on-demand loading and message examples of DingTalk AICard Protocol V0.8; prepared for release, not yet published
---

# DingTalk AICard Protocol V0.8

DingTalk AICard Protocol V0.8 is built on A2UI and provides components, common types, functions and message examples. The package contains four component catalogs, three function catalogs, three common-type files and one message envelope. This grouping supports task-specific reading without changing runtime capabilities. The visual file contains shared value definitions, not a new `style` message field.

The catalogs keep fields, types, constraints and descriptions; component definitions do not embed `examples`. Message examples live separately under `examples/` and are loaded per task.

## Status, versions and authority

This is the **V0.8 specification prepared for publication** of DingTalk AI Card, based on the **A2UI 1.0** baseline pinned in [NOTICE](NOTICE). It is a DingTalk extension and subset, not official A2UI 0.8 or a drop-in implementation of all A2UI 1.0 capabilities.

| Identifier | Meaning |
|---|---|
| DingTalk AI Card V0.8 | The DingTalk specification described by this package |
| A2UI 1.0 | The upstream reference, pinned to the commit in NOTICE |
| Message `version: "v1.0"` | The wire format; do not replace it with V0.8 |
| DWS request `protocolVersion: "1.0"` | The existing transport API value |
| Skill `aicardVersion: "V0.8"` | The DingTalk specification used by that Skill |
| Skill `protocolVersion: "1.0"` | The compatible A2UI wire format, retained for compatibility |
| `catalogId` / Schema `$id` | Runtime / Schema resource identity, not a release number or automatic download instruction |

The public Schemas and requirements in this document jointly define the contract. The examples are explanatory. Lint implements statically checkable requirements using these Schemas and version-matched supplementary rules; its implementation must not introduce undocumented requirements. Passing lint does not establish successful delivery, rendering, or interaction for a particular payload.

This repository layout does not change fields or runtime identifiers. Publish incompatible contract changes under an explicit new specification release with migration and identifier changes documented. Retain prior releases; additions must state client support rather than imply availability on older clients.

## Files and loading order

| File | Contents | When to load |
|---|---|---|
| [catalog-components-common.json](catalog-components-common.json) | 10 frequently used components: Card, Text, Image, Column, Button, Row, Markdown, Divider, Icon, Tag | Start here for ordinary cards |
| [catalog-components-composition.json](catalog-components-composition.json) | 11 composition components: ButtonGroup, Loop, Link, CardHeader, ColumnLayout, Stack, Tabs, List, GridLayout, ScrollView, CollapsiblePanel | Grouped actions, repeated content, advanced layouts and collapse |
| [catalog-components-inputs.json](catalog-components-inputs.json) | 14 inputs: TextField, ChoicePicker, CheckBox, DateTimeInput, Slider, Switch, NumberInput, Rating, InputList, CheckboxListMulti, CheckableImageList, ImageUpload, UserPicker, ConversationPicker | Forms, selection and upload |
| [catalog-components-specialized.json](catalog-components-specialized.json) | 12 display and media components: Table, Chart, ProgressBar, ElapsedTime, Countdown, ImageList, ImageCarousel, Video, AudioPlayer, Avatar, AvatarGroup, File | Tables, charts, progress, timers, media and attachments |
| [catalog-functions-core.json](catalog-functions-core.json) | 14 core functions using official A2UI names with DingTalk definitions: required, regex, length, numeric, email, formatString, formatNumber, formatCurrency, formatDate, pluralize, openUrl, and, or, not | Validation, formatting, URL actions and logic |
| [catalog-functions-expressions.json](catalog-functions-expressions.json) | 18 DingTalk expressions: add, sub, mul, div, mod, eq, ne, lt, lte, gt, gte, cond, toDouble, toLong, objectGet, arrayGet, arrayFind, getLength | Computation, conditions, conversions and collection access |
| [catalog-functions-host-actions.json](catalog-functions-host-actions.json) | 7 host actions: copyText, openChat, previewImages, previewVideo, promptText, showConfirm, showModal | DingTalk host interactions; each function description includes its result schema |
| [common-types-basic.json](common-types-basic.json) | 18 basic common types | Follow the `$ref` of a field you are using |
| [common-types-extended.json](common-types-extended.json) | 6 extended common types for dynamic objects, host arguments and writeback constraints | Follow the `$ref` when needed; common components can depend on it too |
| [common-types-visual.json](common-types-visual.json) | 3 visual common types: ColorToken, SizeToken and IconName | Read when choosing colors, font sizes or named icons |
| [agent-to-renderer.json](agent-to-renderer.json) | Four supported message branches and the component validation entry | Message structure and integration-side validation |

1. Start from the relevant component group and one matching [example](examples/README.md). For simple cards, the common component definitions are sufficient; load a scenario only when its interaction or update sequence is useful.
2. Add other component or function definitions only when needed. Read referenced common-type entries as needed; the model does not need all three common-type files or every function catalog in its initial context.
3. Build messages from the definitions you have read. The integration-side validator must resolve the complete dependency graph for its selected scope, regardless of how little text the model needed to read.

Keep all eleven Schema files in the same relative location and update them as a set; relative paths resolve against the directory of the current file. Reusable types `ColorToken`, `SizeToken` and `IconName` are defined once in `common-types-visual.json` and referenced by components through relative `$ref`, rather than expanding the enum or object structure again.

The names and purposes of `Text.sizeToken` are collected in `common-types-visual.json#/$defs/SizeToken`; read them entry by entry from `const` plus `description`. Send the full `common_*_text_style__font_size` ID — a bare style name or a design name such as `font-size/body` is not accepted. The trailing string branch preserves an existing host compatibility rule and does not mean arbitrary names are supported; a token the host has not registered falls back to `variant`. `common_caption_text_style` only has a UIFont method and is not listed as a known SizeToken.

`basic`, `extended` and `visual` describe only the common-type grouping. `core` groups foundational validation, formatting, logic and URL functions. Their names come from A2UI 1.0, but the file is not an unmodified official catalog: DingTalk retains its own arguments and return semantics, including boolean validation functions. File grouping does not create a new runtime catalog. The former `catalog-basic.json` and `catalog-extended.json` have been replaced; consumers that hard-code those filenames must update their loading configuration as a unit.

## Message examples

The [example index](examples/README.md) provides four adaptable scenarios: form interaction, host-action writeback, agent progress and data reports. It lists their component and function shards. Start with one relevant example; add a specific section from another when the task needs an additional mechanism. Single-component contracts remain in the catalogs; component regression fixtures are maintained outside this distribution.

Every example is a complete new-card JSON message array: `createSurface`, root `updateDataModel`, `updateComponents`, and any later scenario updates. Replace its `surfaceId` consistently before sending a new card. A host-created Surface or an existing-card update has a different message boundary; follow the [delivery boundaries](#surface-initialization-and-updates) below. The examples pass static validation but have not been verified on devices.

When using an example-loading interface, explicitly select the files required by the task. Pointing the official SDK's `examples_path` at the whole directory loads every `*.json` without filtering by task; `include_examples` controls whether they are added to the prompt.

The runtime public catalog identifier is `https://dingtalk.com/card/a2ui/catalogs/public/catalog.json`. All 7 host actions must set `urn:dingtalk:a2ui:host:v1` explicitly in `action.functionCall.catalogId`; a catalog identifier on the surface or on the component does not substitute for it. `catalogId` is a runtime identifier, not a download URL.

`promptText` opens a single-line input panel. Read the [promptText definition](catalog-functions-host-actions.json#/functions/promptText) and its [standalone example](examples/host-action.json) first, then distinguish arguments from results:

| Field | Purpose |
|---|---|
| `args.initialValue` | Preset text; accepts a string or a static binding such as `{ "path": "/preset/text" }` |
| `args.title` | Title of the input panel |
| `args.message` | Hint text on desktop; currently not shown on mobile and never used as the input value |
| Result `data.text` | The text confirmed by the user; do not write it as the input argument `args.text` |

A `promptText` object with the same name in the data model is not passed to the function automatically — reference it explicitly through `initialValue`. To persist the result, configure the write-back path in the component's `metadata.extensions.dt_actionBindingsV1.action.resultPath`; the host-action scenario demonstrates both preset input and reading the successful result at `/ui/prompt/data/text`. Before invocation that output is absent; cancellation preserves it, and a failed result has no `data.text`.

## Surface initialization and updates

Choose the message sequence from the delivery operation:

| Delivery operation | Initial state and messages |
|---|---|
| DWS `chat message send-a2ui-card`, or a complete snapshot loaded from an empty state | Send `createSurface` with an explicit `catalogId`, then initialize the root Data Model and components |
| Content initialization through a host that has already created the surface | Send `updateDataModel` and `updateComponents`; do not create the surface again |
| DWS `chat message update-a2ui-card` for an existing card | Reuse the card's `bizId` and send the actual changes; do not replay `createSurface` or unrelated initial form values |

The four examples already include `createSurface` with the public `catalogId`, a root Data Model update with real initial values, and the initial component tree. They can be adapted as complete new-card files without prepending another creation message or an empty data update. Keep the messages in order and choose a fresh, consistent `surfaceId` for each new Surface. This initialization order is a DingTalk delivery requirement, not a general A2UI 1.0 ordering rule.

For a host-created empty Surface, omit the example's `createSurface` and use its data and component initialization with the host's `surfaceId`. For an existing card, construct only the changes actually required: deleting `createSurface` from a full example would replay initial values and may reset user input. An active Surface cannot be created again without first being deleted. Generic static lint can validate host-created content, so a successful lint result does not establish that a file satisfies the new-card initialization sequence; check it separately at the sending boundary.

These examples contain the inner A2UI message objects. DWS `--content` takes an encoded JSON string array: serialize each message object, then serialize the array of those strings. Do not pass the raw object array as the DWS parameter. `FULL_DATA` and `DELTA` belong to the DingTalk CoRecord transport layer:

| What you send | Message form |
|---|---|
| Through an integration interface that wraps for you | Pass the A2UI message object as the interface requires and let it handle the transport conversion |
| A hand-built `FULL_DATA.a2uiMessages` | An array of objects; a full snapshot must include the host-maintained `createSurface` |
| A hand-built `DELTA.a2uiMessages` | An array of JSON strings; stringify each update message once |

Do not treat the example array as a single message, and do not stringify again for an interface that already wraps.

## Data model and binding

Initialize data with `updateDataModel` and bind it using `{ "path": "/form/name" }`. Absolute paths start at the surface data model. Relative paths are interpreted in the current template context; use them within `Loop` item templates and query `@index` in that context. Update components by stable IDs and send the changes needed for that operation rather than replaying user-editable initial data.

The [message envelope](agent-to-renderer.json) defines the exact update and deletion forms. [DataBinding](common-types-basic.json#/$defs/DataBinding), [ChildList](common-types-basic.json#/$defs/ChildList) and [Loop](catalog-components-composition.json#/components/Loop) define their structures. Static validation does not verify whether references or bound values exist at a particular rendering step.

## Integration and validation

Public definitions use `FunctionCall` for function invocations, `Action` for actions, and the existing `metadata` field for extension data. Value-versus-action classification and event-only slots are enforced by the integration's lint layer, not by separate public helper types. Raw schema validation checks structure; it does not replace those usage checks. Keep the lint implementation and its internal rules in sync with the protocol version.

Dynamic values invoke value functions; `action.functionCall` invokes action functions. Event-only component slots accept `action.event`, not `functionCall`. A host action such as `copyText` is not a Text value expression, a boolean operator such as `not` is not a button action, and `CheckBox.action` is event-only. The companion lint enforces these distinctions without exposing private helper types as public catalog definitions.

The standalone Skill ships `scripts/aicard_lint.py` and `scripts/a2ui-validation-rules.json` together. Prepare its interpreter with `scripts/setup_env.py`, then use that interpreter to run `scripts/aicard_lint.py <file> --format json`. Integrated DWS provides `dws aicard lint --file <file> --format json` and embeds the same rules and protocol without requiring Python or an installed Skill.

| Evidence | What it proves |
|---|---|
| Public Schema validation | Fields, types, enums and structural combinations |
| Companion lint | Those checks plus documented function-category and slot constraints |
| Request acceptance | The service accepted a request, not that it was delivered |
| Same-target readback | The intended target contains the delivered content |
| Client rendering / interaction verification | The tested client rendered / executed the observed behavior |

Lint does not merge runtime state, check graph closure or initial bindings, execute functions, or grade layout. Query a named definition using Python `--explain` or `dws aicard explain`; this authoring view does not replace the full validator. Recursively following every reference from a dynamic value can pull in unrelated functions; it is not a task-specific pruning strategy.

- Extension addresses inside descriptions are for discovery; the actual loading is done by agent tooling or the integration layer. `$ref` expresses the real field dependency.
- Assemble the full common types and catalog for the scope you chose, and build a combined component and function validation entry at the same time. Loading an extended fragment on its own is not a substitute for assembling.
- Validate components and actions against the full contract of the chosen scope before sending. Errors such as a missing host `catalogId`, or writing the `promptText` preset argument as `text`, must be rejected at this stage.
- Validate the complete message structure with `agent-to-renderer.json` in this directory. It is adapted from the official `agent_to_renderer.json` of the same version: its component entry unions the four component catalogs, and common types link the three function catalogs. Register all ten dependency files by their `$id` with a local resolver; no prior assembly is needed. The complete official envelope also declares unsupported remote-function messages and cannot be combined directly with this subset. Assembly-based validators must bind this package’s envelope to the selected assembled catalog and common types.
- This package's envelope does not accept the top-level `profile` field; legacy messages containing that field fail validation against this Schema. New integrations do not need to declare `profile`.
- Component-tree references, dynamic-value evaluation, host authorization and actual rendering behaviour still need verification through the integration pipeline.

All seven catalogs pass the official A2UI v1.0 `catalog_definition.json` meta-contract: `$defs` contains only `anyComponent` and `anyFunction`, a nonempty component union carries the `component` discriminator, and functions use the official wire-level `FunctionCall` structure. A component-only shard has an empty `functions` map and `anyFunction: false`; a function-only shard has an empty `components` map and `anyComponent: false`. An empty `oneOf` array is invalid and is never emitted. The system function `@index` lives in `IndexSystemFunction` in the common types. Each host function description includes its complete result schema next to its arguments; these results are not shared input types.

This protocol includes DingTalk extensions. Its common types derive from upstream definitions, with some retained unchanged and others adjusted through reference redirection or DingTalk customization. To avoid duplicating component definitions, the catalogs reference DingTalk common types through relative `$ref`. A relative `$ref` is a valid JSON Schema URI reference, and `./common-types-basic.json#/$defs/...` uses the same resolution mechanism as the same-directory relative references in the official custom-function examples. The compatibility boundary is not the relative-reference syntax but the reference targets, which include DingTalk custom common types and therefore go beyond the textual whitelist in the v1.0 specification ("external references point only to the official built-in common types"). The accurate description is therefore "official catalog structure plus DingTalk common-type extensions", not a drop-in replacement for the original `common_types.json`.

The shards reduce single-file size and support on-demand loading. A2UI v1.0 does not define automatic discovery or merging of multiple shards under one `catalogId`, so a shard is not a complete runtime catalog that can be handed to a generic official SDK as is. All seven catalog shards and the assembled result pass the official `catalog_definition.json` meta-contract. SDK integrations must assemble the selected definitions and common types; direct validation with this package's envelope instead resolves its local shard references. Neither approach changes runtime catalog identifiers.

## Compatibility

### Compatibility with A2UI 1.0

The seven catalogs use the official catalog structure, but this package is a DingTalk extension and subset. Structural validation against the A2UI 1.0 meta-contract does not establish interoperability with official renderers or SDKs.

| Area | DingTalk V0.8 boundary |
|---|---|
| Messages | Four branches are exposed; `callRendererFunction` and `agentFunctionResponse` are not supported. |
| `Action` | Adds `responsePath` and `wantResponse`, and omits upstream `userMessage`. The added fields do not guarantee an automatic response loop. |
| `CheckRule` | `condition` is a boolean expression and `message` is required; upstream `condition` yields a `ValidationResult`. |
| Common types and references | DingTalk types and relative `$ref` targets extend the upstream catalog's textual type whitelist. The common-type files cannot replace upstream `common_types.json`. |
| Visual fields | Numeric sizes such as `gap`, `padding` and `cornerRadius` are in px; host-resolved ColorToken IDs have no fixed protocol color or opacity. These are DingTalk-specific visual controls. |
| Host features | Host functions, user/conversation pickers, uploads and `@` mentions depend on DingTalk integration. `<a atId>` currently falls back to plain text without server-side ID resolution. |
| Accessibility | Supply meaningful accessibility metadata for assistive technologies. Schema validation checks its structure, not label quality or the usability of a particular card. |

DingTalk AI Card's published components, functions, expressions, events, and visual effects support rendering and interaction on **iOS, Android, HarmonyOS, Windows, macOS, and Web**.

Component fields are closed by `unevaluatedProperties: false`; undeclared fields fail validation. The Schema validates structure, not content security: URL schemes, Markdown filtering and host authorization need integration-side policy. A catalog entry, static lint result or successful request does not prove delivery, rendering or interaction.

### Terms

| Term | Meaning |
|---|---|
| AI Card / A2UI | DingTalk's declarative card format / the upstream Agent-to-UI protocol referenced at version 1.0. |
| Agent / Renderer | The message producer / the client interpreting messages and rendering the interface. |
| Surface | An identified component-tree and data-model container, created by the host or by `createSurface` according to the delivery route. |
| Message / Component | One envelope operation / one typed UI element with an `id`; child references describe composition. |
| Data model / Data binding | Surface state / a `{ "path": ... }` reference into that state or the current item template. |
| Catalog / Host action | Component and function definitions / a DingTalk-host function with an explicit host catalog identity. Physical shards are not distinct runtime catalogs. |
| Action / Loop template | An agent event or renderer function subject to its component slot / an item-context component template. |
| Pattern / Style guide | Adaptable authoring recommendations, not fixed JSON or validation rules. |
| Schema validation / Structural lint | Public JSON Schema checks / Schema plus version-matched static slot and function constraints. Neither simulates runtime state or layout. |
| Delivery / Rendering / Interaction verification | Same-target readback / an observed result on a named client version / an observed callback or state change after an action. |
| Template asset service | A separately planned registry, not part of this package and unrelated to a Loop template. |

## Licence

Parts of this package derive from the A2UI protocol project (Apache-2.0); the upstream baseline is specification v1.0 (status: Candidate), commit `f5e945a93d` (2026-09-15). The full licence text is in [LICENSE](LICENSE); the derivation scope, per-file modifications and trademark boundary are in [NOTICE](NOTICE).

This package is maintained by DingTalk and is neither certified nor endorsed by the A2UI project. The common-type files as a whole are not a drop-in replacement for upstream `common_types.json`; see NOTICE for retained definitions and modifications.
