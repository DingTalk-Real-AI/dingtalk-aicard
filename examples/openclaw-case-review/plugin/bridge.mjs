export const A2UI_BRIDGE_SYMBOL = Symbol.for('@dingtalk-connector/a2ui-bridge');
export const REPORT_SOURCE_SYMBOL = Symbol.for('@case-highlight-a2ui/report-source');

export function getA2UIBridge() {
  const bridge = globalThis[A2UI_BRIDGE_SYMBOL];
  if (!bridge || typeof bridge.getCurrentContext !== 'function'
    || typeof bridge.createCurrentCard !== 'function'
    || typeof bridge.updateCard !== 'function'
    || typeof bridge.onCardAction !== 'function') return undefined;
  return bridge;
}

export function installReportSourceProvider(provider) {
  if (typeof provider !== 'function') throw new Error('REPORT_SOURCE_FUNCTION_REQUIRED');
  if (typeof globalThis[REPORT_SOURCE_SYMBOL] === 'function') throw new Error('REPORT_SOURCE_ALREADY_INSTALLED');
  globalThis[REPORT_SOURCE_SYMBOL] = provider;
  return () => {
    if (globalThis[REPORT_SOURCE_SYMBOL] === provider) delete globalThis[REPORT_SOURCE_SYMBOL];
  };
}
