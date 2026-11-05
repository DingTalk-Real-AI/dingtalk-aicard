# OpenClaw project review card example

This example shows how an OpenClaw business plugin can use a Connector-provided A2UI bridge for a private project-review card:

- render verified project facts and a fallback-meeting choice;
- collect the meeting date and time;
- show a server-generated proposal summary;
- require a second confirmation;
- preserve form input when returning to edit;
- apply a sandbox result and update the same card.

The example is intentionally business-neutral. It contains no DingTalk tenant IDs, user IDs, robot codes, document IDs, or credentials. The deployment config supplies the approved private scope, one DWS Profile, the local DWS JS entry point, and the report document and date range.

Configure the OpenClaw plugin locally with deployment-owned values; do not commit the filled file:

```json
{
  "privateTest": {
    "accountId": "<connector-account-id>",
    "senderId": "<approved-user-id>",
    "sessionKey": "<trusted-private-session-key>",
    "robotCode": "<original-robot-code>"
  },
  "report": {
    "profile": "<corp-id:user-id>",
    "dwsCliEntry": "<absolute-path-to-dws.js>",
    "documentId": "<report-document-id>",
    "periodStart": "2027-03-01T00:00:00+08:00",
    "periodEnd": "2027-04-01T00:00:00+08:00",
    "enableWrites": false
  }
}
```

The `integration/dws-cli-reader.mjs` adapter uses OpenClaw's public `runUtf8CommandWithTimeout` process helper (OpenClaw 2026.10.9 or later) to run only fixed read-only DWS CLI argument vectors. The companion `integration/dws-cli-writer.mjs` exposes only fixed document str-replace, calendar reschedule, and calendar delete operations, and separate document/event readback methods. Both bound execution time and output, request process-tree cleanup, and make no retries. It follows returned calendar cursors. `integration/dws-report-source.mjs` requires complete reads, unique confirmed meetings, and exposes document/calendar attendee differences. DWS authentication remains in the local DWS profile; no token is stored in the plugin.

The `plugin/business-test.mjs` state machine rejects untrusted callbacks and checks the card instance, user, nonce, revision, TTL, action allowlist, and callback idempotency before updating the card. The Connector bridge remains responsible for robot identity, card creation/update, and the existing Stream connection. The public example defaults to a local sandbox. Set `report.enableWrites` to `true` only in a deployment-owned configuration after private-card acceptance; the confirm action then performs document write/readback before calendar write/readback and shows the result on the same card.

These files are an example integration layer, not a Connector feature or a production DWS workflow. They require the optional `@dingtalk-connector/a2ui-bridge` capability and remain inactive if it is absent. The Connector owns app authentication, robot delivery, and the existing Stream callback; the business plugin owns authorization scope, card state, and sandbox actions. Validate the payload with `dws aicard lint`, then verify delivery, client rendering, callbacks, business writes, and readback in the target deployment.
