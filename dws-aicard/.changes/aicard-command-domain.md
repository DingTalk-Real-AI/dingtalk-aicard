---
category: Added
---

- Add `dws aicard explain <name>`, `lint`, and `preview`. The embedded DingTalk A2UI protocol supports native offline lookup and structural validation without Python. Preview validates a complete new-card file and sends it to the current user's direct chat; dry-run does not send. Include the DWS authoring Skill and report request acceptance separately from client-rendering evidence.

- Add `aicard template create/get/update` for cloud template assets. Calls need `--mcp-id` or `DWS_AICARD_TEMPLATE_MCP_ID` for the target environment, including dry-run. Writes are not retried automatically, `get` does not overwrite existing files, and results return the template ID, version and content digest. Template operations do not send cards or change business bindings; diagnostic logs omit template bodies.

- Add `preview --support-forward`, defaulting to false. Preview results report the fallback summary and forwarding choice.

- Resource and new-card preflight reject inline `data:` URLs in resource fields with `resource.inline_data_unsupported`; preview rejects them before calling the service. Markdown containing `data:` text gets a nonblocking `resource.markdown_data_unverified` warning. Prefer verified HTTPS assets; validation does not prove client image support.
