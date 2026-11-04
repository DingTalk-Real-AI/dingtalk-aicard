const record = value => !!value && typeof value === 'object' && !Array.isArray(value);
const nonEmpty = value => typeof value === 'string' && value.trim().length > 0 && value.length <= 1024;

export function parsePluginConfig(value) {
  if (!record(value) || !record(value.privateTest) || !record(value.report)) {
    throw new Error('PRIVATE_TEST_AND_REPORT_CONFIG_REQUIRED');
  }
  const scope = value.privateTest;
  const report = value.report;
  for (const key of ['accountId', 'senderId', 'sessionKey', 'robotCode']) {
    if (!nonEmpty(scope[key])) throw new Error(`PRIVATE_TEST_${key.toUpperCase()}_REQUIRED`);
  }
  for (const key of ['profile', 'dwsCliEntry', 'documentId', 'periodStart', 'periodEnd']) {
    if (!nonEmpty(report[key])) throw new Error(`REPORT_${key.toUpperCase()}_REQUIRED`);
  }
  const periodStart = Date.parse(report.periodStart);
  const periodEnd = Date.parse(report.periodEnd);
  if (!Number.isFinite(periodStart) || !Number.isFinite(periodEnd) || periodStart >= periodEnd) throw new Error('REPORT_PERIOD_INVALID');
  return Object.freeze({
    policy: Object.freeze({
      accountId: scope.accountId,
      senderId: scope.senderId,
      sessionKey: scope.sessionKey,
      robotCode: scope.robotCode,
    }),
    report: Object.freeze({
      documentId: report.documentId,
      profile: report.profile,
      dwsCliEntry: report.dwsCliEntry,
      periodStart: report.periodStart,
      periodEnd: report.periodEnd,
      ...(nonEmpty(report.profile) ? { profile: report.profile } : {}),
    }),
  });
}
