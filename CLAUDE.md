# Repository guidance for Claude

Read and follow [AGENTS.md](AGENTS.md) for repository maintenance rules.

## Repository responsibilities

`skills/dingtalk-aicard/` is the standalone Skill for creating and editing A2UI
JSON, with offline Python contract lookup and validation. DWS is not required;
requested sending is delegated to an available delivery capability.

`dws-aicard/` is the module integrated into DWS for A2UI authoring and delivery.
It contains the generated DWS Skill, embedded protocol, native Go validator,
and `dws aicard explain`, `lint`, and `preview` commands. The Skill guides JSON
authoring; `preview` creates and sends a real card to the current user. Sending
to other people or groups and updating or finishing cards use DWS
`chat message send-a2ui-card` and `chat message update-a2ui-card`, not an
`aicard send` command. Repository generation is separate from installation and
building in an external DWS checkout.

## Language of repository content

Write new or modified developer-facing prose in English, including Skill
instructions, documentation, test names, CLI help, diagnostics, and public
example explanations. Use English display copy in public authoring examples
by default. Preserve exact protocol identifiers and literals. Intentional
localized payloads, classification terms, internationalization inputs, real
paths, legacy migration strings, and the protocol NOTICE's bilingual legal
summary may retain their original language; document their purpose. For
generated files, change the authoritative source and regenerate.
This rule concerns repository content; communicate with users in their
requested language.
