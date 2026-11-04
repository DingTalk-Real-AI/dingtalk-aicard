# OpenClaw project review card example

This example shows how an OpenClaw business plugin can use a Connector-provided A2UI bridge for a private project-review card:

- render verified project facts and a fallback-meeting choice;
- collect the meeting date and time;
- show a server-generated proposal summary;
- require a second confirmation;
- preserve form input when returning to edit;
- apply a sandbox result and update the same card.

The example is intentionally business-neutral. It contains no DingTalk tenant IDs, user IDs, robot codes, document IDs, credentials, or live DWS client. The host supplies those through deployment configuration and a trusted report provider.

The `integration/dws-report-source.mjs` adapter is read-only. It accepts injected document and calendar readers, requires complete calendar pagination and unique confirmed meetings, and exposes document/calendar attendee differences. A production adapter must use the same authenticated DWS profile for both reads, then implement writes and readback separately after user acceptance.

The `plugin/business-test.mjs` state machine rejects untrusted callbacks and checks the card instance, user, nonce, revision, TTL, action allowlist, and callback idempotency before updating the card. The Connector bridge remains responsible for robot identity, card creation/update, and the existing Stream connection.

These files are an example integration layer, not a Connector feature or a production DWS workflow. Validate the payload with `dws aicard lint`, then verify delivery, client rendering, callbacks, business writes, and readback in the target deployment.
