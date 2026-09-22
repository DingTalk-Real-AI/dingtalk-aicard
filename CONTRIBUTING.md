---
status: reference
updated: 2026-09-21
export-target: dingtalk-aicard/CONTRIBUTING.md
link-base: exported-repository-root
scope: Maintenance and verification of the DingTalk AI Card V0.8 public distribution
---

# Maintaining the public distribution

## License and contribution terms

By submitting a contribution for inclusion in this repository, you agree that
it is licensed under the [Apache License, Version 2.0](LICENSE). Submit only
material you are authorized to contribute. Identify third-party or
upstream-derived material and retain its required license and attribution;
the protocol provenance is recorded separately in `spec/NOTICE`.

`spec/` is the public release snapshot, generated from the maintained protocol sources. Do not maintain independent hand-edited copies in the standalone or DWS Skill. Proposed protocol changes must be applied to the maintained source and imported as one release; Skill behavior and validator code are maintained in this repository.

## Local rebuilds

A checkout includes all DingTalk protocol inputs, supplementary rules and regression fixtures. These commands do not require a sibling documentation repository. Prepare a healthy Python environment with `skills/dingtalk-aicard/scripts/setup_env.py` and use its returned interpreter below.

```bash
"$AICARD_PYTHON" tools/sync_protocol.py --check
"$AICARD_PYTHON" tools/sync_protocol.py
"$AICARD_PYTHON" tools/test_aicard_lint.py
"$AICARD_PYTHON" tools/test_schema_engine.py
"$AICARD_PYTHON" tools/test_sync_protocol.py
"$AICARD_PYTHON" tools/test_conformance.py
```

Local rebuilds use `spec/`, the bundled validation rules and `shared/fixtures/`. They regenerate indexes, the DWS Skill and Go assets together. `--check` never writes. Changes to registered distribution inputs require an upstream import; unexpected edits to derived files are rejected. `--force` is only for explicitly discarding edits to managed artifacts.

`shared/.protocol-sync.json` records portable source identities, protocol versions and file hashes. It contains no maintainer-specific absolute paths. Regression fixtures are repository test data, not installed Skill context. Do not remove them to reduce the Skill package size.

## Maintainer imports

Use explicit upstream paths. Keep matching public resources, supplementary rules and regression fixtures from the same source revision:

```bash
"$AICARD_PYTHON" tools/sync_protocol.py \
  --source /path/to/card-docs/protocol/a2ui/open \
  --validation-rules /path/to/card-docs/tools/a2ui-open-package/validation-rules.json \
  --conformance-source /path/to/card-docs/protocol/a2ui/conformance-fixtures \
  --repository-docs /path/to/card-docs/reference/dingtalk-aicard-public
```

The importer prepares a repository-local candidate, validates it, regenerates all derived artifacts, checks for concurrent input changes, and publishes with rollback on write failure. The documented sources of the repository README and this guide are imported with `--repository-docs`.

## Official reference and release checks

The reference is A2UI 1.0 at commit `f5e945a93da36a5d126228f2ad6fbe68809ef8f7`, as recorded in `spec/NOTICE`. Prepare that upstream checkout explicitly. Release verification must fail if required upstream test cases are absent:

```bash
"$AICARD_PYTHON" tools/conformance.py \
  --official /path/to/pinned-a2ui/specification/v1_0/test/cases --require-official
"$AICARD_PYTHON" tools/build_go_assets.py --check
```

Every intentional difference from upstream is registered in `conformance.py`. A development run may report skipped upstream checks; it is not a complete release run. A successful structural test does not certify any client platform.

## Native DWS build

Install the source mirror into the intended DWS checkout using `tools/install_to_dws.py --dws /path/to/dws --check`, review the plan, then run it without `--check`. This command installs source, not a compiled executable. Follow that checkout's build and test instructions and use its newly built binary for the comparison:

```bash
"$AICARD_PYTHON" tools/conformance.py \
  --cmd '/path/to/new/dws aicard lint --file {file} --format json' \
  --explain-cmd '/path/to/new/dws aicard explain {name} --format json' \
  --official /path/to/pinned-a2ui/specification/v1_0/test/cases --require-official
```

DWS does not distribute Python scripts. `tools/build_go_assets.py` derives explain output, Unicode tables and supplementary rules from the standalone implementation; its manifest binds them to the embedded public Schemas.

## Before public release

Verify references and links, all generated copies, both validator implementations, and the complete test suites. Check the installed Skill outside the repository. Retain the repository and each Skill's Apache-2.0 LICENSE/NOTICE, plus the nested protocol LICENSE/NOTICE, in every independently distributed package. Confirm the approved publication scope and review tracked files and Git history for private configuration, credentials, real user data and unapproved assets. These release checks and client evidence are not implied by passing local tests.
