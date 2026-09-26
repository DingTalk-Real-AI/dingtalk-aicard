# Repository maintenance rules

## Repository responsibilities

- `skills/dingtalk-aicard/` is the standalone A2UI authoring Skill. It guides
  creation and editing of A2UI JSON and provides offline contract lookup and
  validation through Python. It does not require DWS; sending is delegated to
  an available delivery capability when requested.
- `dws-aicard/` is the module integrated into DWS for the A2UI authoring and
  delivery workflow. It includes the generated DWS Skill, embedded protocol,
  native Go validator, and `dws aicard explain`, `lint`, and `preview` commands.
  The Skill guides JSON authoring; the commands query contracts, validate
  payloads, and create and send a real self-preview. Sending to other people
  or groups and updating or finishing cards use the existing DWS
  `chat message send-a2ui-card` and `chat message update-a2ui-card` commands,
  not a separate `aicard send` command.
- Keep authoring guidance, native execution, and delivery responsibilities
  distinct. Generating the DWS Skill here does not install or build the module
  in an external DWS checkout.

## Language of repository content

Write new or modified developer-facing prose in English, including Skill
instructions, reference guidance, documentation, code comments, test names,
CLI help, diagnostics, and public example explanations. Use English display
copy in public authoring examples by default. Preserve protocol identifiers
and exact literals. Localized card payloads, corpus classification terms,
Unicode or internationalization test inputs, real file paths, exact legacy
migration strings, and the protocol NOTICE's bilingual legal summary may keep
their original language when translation would change their purpose. Mark
intentional exceptions clearly in the surrounding test or documentation; do
not treat every non-ASCII character as untranslated prose. For generated
files, change the authoritative source and regenerate. Check changed lines
before completing a task. This rule concerns repository content; communicate
with users in their requested language.

## Keep both Skill deliverables in sync

`skills/dingtalk-aicard/` is the standalone Skill and the source for the DWS Skill.
`dws-aicard/skills/multi/dingtalk-aicard/` is generated, not independently maintained.
After changing the source Skill or `tools/build_dws_skill.py`, regenerate and check
the DWS edition before delivery. Protocol imports and indexes follow their own
upstream generation paths; do not edit their generated files directly.

`spec/` retains the full protocol. Its main README is for repository readers
only and is not distributed with either Skill. Compatibility boundaries and
terms are in that README; retired standalone pages are not imported.
`tools/sync_protocol.py` uses a distribution manifest for the standalone Skill
and generates its example navigation from the local `EXAMPLE_GUIDANCE` mapping,
independently of imported README prose. Keep the mapping aligned with example JSON files.
Keep Schema files, example JSON, LICENSE, and NOTICE unchanged when editing
Skill guidance. DWS `references/` must remain byte-identical to the standalone
`references/`; do not remove files from the DWS copy alone.

| Content | Synchronization rule |
|---|---|
| `references/` | Synchronize every file byte-for-byte, including `design.md`, `patterns/`, examples, indexes, and protocol; additions, edits, and deletions all count |
| Common `SKILL.md` body | Keep one source and synchronize through the generator |
| Execution guidance in `SKILL.md` | Keep the `runtime`, `query`, `lint`, and `delivery` marker pairs; `DWS_EXECUTION_BLOCKS` adapts them |
| `scripts/` | Distribute only with the standalone Skill; DWS uses native Go commands |
| Top-level `LICENSE` and `NOTICE` in the Skill | Include with the standalone Skill and copy byte-for-byte into the generated DWS Skill; keep the protocol's nested notices too |
| Other new top-level files or directories | Decide whether DWS should receive them and update the generator and checks if needed |

The standalone edition queries and validates with Python and does not require DWS.
The DWS edition uses `dws aicard explain`, `lint`, and `preview`; it cannot
silently fall back to Python and claim DWS validation. When execution behavior
changes, review the DWS adaptation as well as the common text.

## Completion conditions for each change

1. Inspect the worktree diff before regenerating. The generator rebuilds the
   DWS Skill directory. Preserve any independent DWS edits not yet incorporated
   into the source or adaptation blocks.
2. From the repository root, run and check exit codes for:

   ```bash
   python3 tools/build_dws_skill.py
   python3 tools/build_dws_skill.py --check
   ```

   The generator uses only the Python standard library. Design or pattern edits
   alone do not require installing lint dependencies.

3. Inspect the final diff for missing source or generated changes, including
   additions and deletions. If the generator changes, run
   `python3 tools/test_build_dws_skill.py`. Put test temporary directories
   inside the workspace; never write to `/private/tmp`.
4. If the protocol, supplementary validation rules, or explain behavior
   changes, also check the Go implementation and embedded assets. Run
   `tools/build_go_assets.py` and relevant conformance tests as needed.
   Copying Skill documentation does not update the DWS executor.
5. Deliver or commit source and corresponding generated output together. Do
   not claim the two editions are synchronized unless
   `build_dws_skill.py --check` passes. If a source edit produces no DWS
   change, a passing check is sufficient.

`tools/conformance.py` and `tools/install_to_dws.py` include synchronization
checks, but ordinary edits do not run them automatically.

## Boundary of the external DWS repository

The rules above synchronize the two editions within this repository.
Installing into an external checkout such as `dws-qwenwork` is a separate
operation. When requested, use `tools/install_to_dws.py` with its check and
installation workflow. Distinguish “generated within this repository” from
“installed into the external DWS repository” in completion reports.
