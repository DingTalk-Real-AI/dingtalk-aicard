---
status: authoritative
updated: 2026-09-26
scope: Five task-oriented message examples of DingTalk AICard Protocol V0.8
---

# DingTalk AICard Protocol V0.8 scenario examples

Read one matching scenario when composing bindings, actions or updates. For a single field, read its component or function definition instead. These are adaptable examples, not required layouts.

Each JSON file is a complete new-card message array: createSurface with the public catalogId, root updateDataModel, then updateComponents and any scenario updates. For a host-created empty Surface, omit createSurface; for an existing-card update, construct only the actual changes instead of replaying initial data and components. Keep each surfaceId consistent. See [Surface initialization and updates](../README.md#surface-initialization-and-updates) for delivery boundaries. FULL_DATA / DELTA wrappers are not part of these arrays.

| Example | Demonstrates | Relevant shards |
|---|---|---|
| [form-interaction.json](form-interaction.json) | Persistent field titles, typed initial values, local input state, upload events and submit-button checks | [catalog-components-common.json](../catalog-components-common.json), [catalog-components-inputs.json](../catalog-components-inputs.json), [catalog-functions-core.json](../catalog-functions-core.json) |
| [host-action.json](host-action.json) | Explicit host catalog, promptText result writeback and item-owned button metadata | [catalog-components-common.json](../catalog-components-common.json), [catalog-components-composition.json](../catalog-components-composition.json), [catalog-functions-host-actions.json](../catalog-functions-host-actions.json) |
| [agent-run-progress.json](agent-run-progress.json) | In-progress execution panels with AIGenerating, shimmer, loading GIFs and distinct tool icons | [catalog-components-common.json](../catalog-components-common.json), [catalog-components-composition.json](../catalog-components-composition.json) |
| [agent-run.json](agent-run.json) | The same execution flow with a final one-shot Markdown and File result outside the panels | [catalog-components-common.json](../catalog-components-common.json), [catalog-components-composition.json](../catalog-components-composition.json), [catalog-components-specialized.json](../catalog-components-specialized.json) |
| [data-report.json](data-report.json) | Tabs, local table pagination, chart data, relative Loop bindings and Stack references | [catalog-components-common.json](../catalog-components-common.json), [catalog-components-composition.json](../catalog-components-composition.json), [catalog-components-specialized.json](../catalog-components-specialized.json) |

Shard names describe reading dependencies, not runtime catalog IDs. Follow field $refs into the common-type files as needed. Do not load this whole directory into every prompt.

## Interaction notes

- In the form, single selection still binds a string array. `required(false)` passes: the agreement check therefore reads the boolean binding directly. ImageUpload reports only after a successful upload; canceled or failed uploads do not report an event.
- Host actions explicitly use `urn:dingtalk:a2ui:host:v1`. promptText takes `initialValue`, then writes `{status: "success", data: {text: "..."}}` to the declared resultPath. Before a call, result slots are empty; a missing-initial-value warning at these asynchronous output bindings is expected. Cancellation preserves the previous result; a failed result has no data.text. The static hint and bounded result text provide an empty state. ButtonGroup items own their metadata and never inherit a parent writeback path.
- Both agent examples start with two batches (one-based message positions): 1-3 initialize a collapsed but non-empty reading panel; 4 completes reading and starts analysis. agent-run-progress stops here with AIGenerating, shimmer and the analysis loading GIF still active; it demonstrates a running state, not a completed task. Keep that state when demonstrating this example. Only agent-run has message 5 (batch 3), which completes execution and exposes the result. Apply batches in order to the same Surface; applying the entire array at once may show only the final state. Timing belongs to the consuming application, not the JSON.
- Only the outer panel uses reasoning, the DingTalk host convention [AIGenerating], and shimmer. Nested indented tool panels use light/dark loading GIFs while running; completed groups remove those images and use completion text. The completed reading group in agent-run-progress also uses Check_L_outlined in its panel icon field. Panel icons accept image URLs or registered icon names. agent-run retains text-only completion indicators. Tool rows use distinct Folder_L_outlined and CodeProgram_L_outlined icons, secondary text color, and the action-text size token. Panel-title size is renderer-owned; no unsupported size field is added.
- Each newly introduced panel is initially collapsed and already has reachable content. Later definitions omit defaultExpanded; user toggles are maintained locally. Parent children are updated when analysis is added. Only agent-run completion removes shimmer and the generating marker. Shimmer, generation indicators, loading GIFs, and collapse interactions are supported across all six DingTalk platforms. Validate the actual card's resource loading and interactions separately from Schema validation.
- agent-run-progress contains no final answer. In this example, agent-run attaches its complete short Markdown and File outside the panels in batch 3, without streaming the result; this is not a restriction on panel contents. The File links to a public W3C dummy PDF, not a generated report; replace it with a real artifact URL in production. No onPreview callback is configured, so file-event delivery is not demonstrated.
- The table receives its complete dataset once; pageSize 2 with three rows demonstrates local pagination. Loop item bindings are relative to each row. The chart Stack has both a base child and a referenced corner label.

All examples are checked structurally and semantically. Host callbacks and device rendering are not executed by static validation. No user or tool activity is performed by reading these files.
