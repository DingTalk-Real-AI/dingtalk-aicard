---
status: authoritative
updated: 2026-09-21
export-target: dingtalk-aicard/README.md
link-base: exported-repository-root
scope: Public repository entry for DingTalk AI Card V0.8 and its standalone Skill and DWS deliverables
---

# DingTalk AI Card V0.8

Author and structurally validate DingTalk AI cards using a specification based on **A2UI 1.0**, with DingTalk extensions and an explicitly supported subset. V0.8 is the DingTalk specification release; message `version` remains `"v1.0"`.

## Start here

- [Specification](spec/README.md): messages, catalogs, data binding and validation boundaries.
- [Specification compatibility and terms](spec/README.md#compatibility-with-a2ui-10): A2UI 1.0 differences, client evidence boundary and terms.
- [Four scenario examples](spec/examples/README.md).
- [Standalone Skill](skills/dingtalk-aicard/SKILL.md): authoring and offline Python validation.
- [Contributing and reproducible builds](CONTRIBUTING.md).

## Standalone Skill

Copy the complete `skills/dingtalk-aicard/` directory into your agent's skills directory. Keep `SKILL.md`, `references/`, `scripts/` and the bundled license files together. Neither DWS nor the repository root is required after installation.

From a writable task directory, run the installed Skill's `scripts/setup_env.py`. Use the returned `pythonExecutable` for subsequent commands; an existing healthy environment can be reused. Initial dependency installation may require network access; validation itself is offline.

```bash
python3 /path/to/dingtalk-aicard/scripts/setup_env.py
AICARD_PYTHON='<returned pythonExecutable>'
"$AICARD_PYTHON" /path/to/dingtalk-aicard/scripts/aicard_lint.py --explain Tabs --format json
"$AICARD_PYTHON" /path/to/dingtalk-aicard/scripts/aicard_lint.py card.a2ui.json --format json
```

## DWS integration

A DWS build with this module provides native `aicard explain`, `aicard lint` and `aicard preview` commands. The runtime embeds the protocol and validator; no Python environment or separately installed Skill is needed. Copying a Skill into an older DWS does not install native commands.

```bash
dws aicard explain Tabs --format json
dws aicard lint --file card.a2ui.json --format json
```

`preview` sends a real card to the current user and is used only when requested. Use `--dry-run` to inspect its local sending boundary without contacting the service. A receipt does not prove delivery or client rendering.

## What validation proves

Lint checks JSON and protocol structure, including version-matched function/slot constraints. It does not simulate runtime state, check reference closure or binding initial values, evaluate functions, grade layout, or establish rendering/interaction success. The four examples are complete new-card message arrays; adapt their `surfaceId` and follow the [delivery boundaries](spec/README.md#surface-initialization-and-updates) for host-created Surfaces or existing-card updates.

## Licensing

This repository is licensed under [Apache-2.0](LICENSE); see the repository [NOTICE](NOTICE) for attribution and scope. The standalone and DWS Skills each carry their own `LICENSE` and `NOTICE` so they remain attributable when distributed separately. Protocol-derived files also retain their [nested license](spec/LICENSE) and [per-file attribution](spec/NOTICE). Client support remains limited to the evidence described in the [specification compatibility section](spec/README.md#compatibility-with-a2ui-10). Review the exact files and Git history before publishing a release.
