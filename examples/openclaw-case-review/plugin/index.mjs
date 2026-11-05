import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { resolveStateDir } from 'openclaw/plugin-sdk/state-paths';
import { BusinessA2UITest } from './business-test.mjs';
import { getA2UIBridge } from './bridge.mjs';
import { parsePluginConfig } from './config.mjs';
import { createDwsCliReaders } from '../integration/dws-cli-reader.mjs';
import { createDwsCliWriter } from '../integration/dws-cli-writer.mjs';
import { applyDwsDecision } from '../integration/dws-business-writer.mjs';
import { createDwsReportSource } from '../integration/dws-report-source.mjs';

const TOOL = 'project_review_a2ui_private_sandbox';
let registered = false;
let workflow;

function result(value) {
  return { content: [{ type: 'text', text: JSON.stringify(value) }], details: value };
}

export function register(api) {
  if (registered) throw new Error('PROJECT_REVIEW_PLUGIN_ALREADY_REGISTERED');
  if (typeof api?.registerTool !== 'function' || typeof api?.registerService !== 'function'
    || typeof api?.lifecycle?.registerRuntimeLifecycle !== 'function') {
    throw new Error('OPENCLAW_SERVICE_AND_LIFECYCLE_APIS_REQUIRED');
  }
  const { policy, report } = parsePluginConfig(api.pluginConfig);
  const databasePath = join(resolveStateDir(), 'project-review-a2ui', 'sandbox.sqlite');
  const stop = () => {
    if (workflow) { workflow.close(); workflow = undefined; }
  };
  api.registerService({
    id: 'project-review-a2ui-sandbox',
    start() {
      const bridge = getA2UIBridge();
      if (!bridge) return;
      mkdirSync(join(resolveStateDir(), 'project-review-a2ui'), { recursive: true });
      const readers = createDwsCliReaders(report);
      const reportProvider = createDwsReportSource({ ...readers, ...report });
      const writer = report.enableWrites === true ? createDwsCliWriter(report) : undefined;
      const decisionApplier = writer ? params => applyDwsDecision({ writer, ...params }) : undefined;
      workflow = new BusinessA2UITest({ databasePath, bridge, policy, reportProvider, decisionApplier });
      workflow.unsubscribe = bridge.onCardAction?.(event => workflow?.handleAction(event));
    },
    stop,
  });
  api.lifecycle.registerRuntimeLifecycle({ id: 'project-review-a2ui-sandbox-resources', dispose: stop });
  api.registerTool(toolContext => {
    const bridge = getA2UIBridge();
    const context = bridge?.getCurrentContext(toolContext);
    if (!bridge || !workflow || !context || !workflow.isAuthorized(context)) return null;
    return {
      name: TOOL,
      label: 'Project review A2UI card (private sandbox)',
      description: 'Create a project review card for the approved current private chat. Changes remain in a local sandbox; no recipient or business-write arguments are accepted.',
      parameters: { type: 'object', properties: {}, additionalProperties: false },
      async execute(callId, args) {
        if (!args || typeof args !== 'object' || Array.isArray(args) || Object.keys(args).length) {
          return result({ status: 'rejected', reason: 'NO_TOOL_PARAMETERS_ALLOWED' });
        }
        const current = bridge.getCurrentContext(toolContext);
        if (!current || !workflow?.isAuthorized(current)) {
          return result({ status: 'rejected', reason: 'PRIVATE_TEST_SCOPE_NOT_AUTHORIZED' });
        }
        return result(await workflow.create(current, callId));
      },
    };
  }, { name: TOOL });
  registered = true;
}

export default { register };
