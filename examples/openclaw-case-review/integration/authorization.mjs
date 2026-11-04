// Trusted-code-only scope gate. Callback fields and tool arguments never define policy.
export function createPrivateTestAuthorizer(policy) {
  const keys = ['accountId', 'senderId', 'sessionKey', 'robotCode'];
  if (!policy || keys.some(key => typeof policy[key] !== 'string' || !policy[key].trim())) {
    throw new Error('An explicit approved private-test scope is required');
  }
  const pinned = Object.freeze(Object.fromEntries(keys.map(key => [key, policy[key]])));
  return context => {
    try {
      return context?.isDirect === true && context.isCurrent?.() === true
        && context.accountId === pinned.accountId && context.senderId === pinned.senderId
        && context.sessionKey === pinned.sessionKey
        && (context.robotCode ?? context.config?.clientId) === pinned.robotCode;
    } catch { return false; }
  };
}
