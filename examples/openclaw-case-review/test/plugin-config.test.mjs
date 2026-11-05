import test from 'node:test';
import assert from 'node:assert/strict';
import { parsePluginConfig } from '../plugin/config.mjs';

const validConfig = {
  privateTest: { accountId: 'main', senderId: 'user-test', sessionKey: 'dingtalk:direct:user-test', robotCode: 'robot-test' },
  report: { profile: 'corp-test:user-test', dwsCliEntry: '/opt/dws/bin/dws.js', documentId: 'doc-test', periodStart: '2026-11-01T00:00:00+08:00', periodEnd: '2026-12-01T00:00:00+08:00' },
};

test('private identifiers and report location come from deployment config', () => {
  const parsed = parsePluginConfig(validConfig);
  assert.deepEqual(parsed.policy, validConfig.privateTest);
  assert.deepEqual(parsed.report, validConfig.report);
  assert.equal(Object.isFrozen(parsed.policy), true);
  assert.equal(Object.isFrozen(parsed.report), true);
});

test('missing scope and invalid report range are rejected', () => {
  assert.throws(() => parsePluginConfig({ report: validConfig.report }), /PRIVATE_TEST_AND_REPORT_CONFIG_REQUIRED/);
  assert.throws(() => parsePluginConfig({ ...validConfig, report: { ...validConfig.report, periodEnd: 'bad' } }), /REPORT_PERIOD_INVALID/);
});

test('DWS writes remain disabled unless deployment explicitly enables them', () => {
  const parsed = parsePluginConfig({ ...validConfig, report: { ...validConfig.report, enableWrites: true } });
  assert.equal(parsed.report.enableWrites, true);
  const defaultParsed = parsePluginConfig(validConfig);
  assert.equal('enableWrites' in defaultParsed.report, false);
});
