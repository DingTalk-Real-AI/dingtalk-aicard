export const A2UI_BRIDGE_SYMBOL = Symbol.for('@dingtalk-connector/a2ui-bridge');

export function getA2UIBridge() {
  const bridge = globalThis[A2UI_BRIDGE_SYMBOL];
  if (!bridge || typeof bridge.getCurrentContext !== 'function'
    || typeof bridge.createCurrentCard !== 'function'
    || typeof bridge.updateCard !== 'function'
    || typeof bridge.onCardAction !== 'function') return undefined;
  return bridge;
}
