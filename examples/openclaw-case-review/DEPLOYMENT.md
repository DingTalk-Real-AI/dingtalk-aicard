# Deployment and acceptance

This example is deployable only after the compatible Connector A2UI bridge has been merged and released. Do not install a pull-request branch or a private Connector fork into a managed Gateway.

## Release inputs

Record the merged Connector release version and digest, the merged A2UI example commit or package digest, the OpenClaw host version, and the rollback release before changing a host. Keep the filled plugin configuration outside Git and outside the package.

The deployment-owned configuration must provide:

- the approved Connector account and private sender scope;
- one DWS Profile and absolute DWS CLI entry path;
- the report document ID and reporting period;
- `enableWrites: false` for private-card sandbox acceptance.

Set `enableWrites: true` only after the private card has been visibly rendered, clicked by the approved user, updated on the same card, and accepted by the user. The production write setting must remain separate from the card package.

## Activation order

1. Verify the managed Connector is the official released version and exposes the A2UI bridge capability.
2. Install the business plugin from the approved release source and validate its manifest and package contents.
3. Start the plugin with `enableWrites: false` and confirm that the private tool is visible only in the approved direct-chat context.
4. Run the minimum private-card test: robot delivery, user-visible rendering, correct-user click, trusted Stream callback, and same-card update.
5. Exercise the complete form in sandbox mode, including return-to-edit, duplicate click, expired confirmation, stale revision, and failed-update paths.
6. After explicit user acceptance, enable DWS writes. The confirm action re-reads the report, writes and reads back the document decision, then writes and reads back the fallback event. A document failure stops before calendar work; a calendar failure is reported as partial completion without replay.
7. Only after the private flow and DWS readbacks pass should a separate change enable the scheduled 09:00 workflow.

## Rollback

Disable the business plugin or restore the previous official release using the recorded release digest. Do not delete the business database before preserving its receipts and audit state. If a DWS write returns an uncertain result, read the document and event first; never replay the write automatically.

Successful API acceptance, client rendering, callback receipt, same-card update, business write, and writeback readback are separate acceptance facts. Record each one independently.
