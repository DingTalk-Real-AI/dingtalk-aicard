import { isAbsolute } from 'node:path';

const valid = value => typeof value === 'string' && value.trim().length > 0 && value.length <= 2048;

async function hostCommand(argv, options) {
  const { runUtf8CommandWithTimeout } = await import('openclaw/plugin-sdk/process-runtime');
  return runUtf8CommandWithTimeout(argv, options);
}

async function runJson(commandImpl, executable, entry, args, timeoutMs, maxBuffer) {
  let result;
  try {
    result = await commandImpl([executable, entry, ...args], {
      timeoutMs,
      killProcessTree: true,
      requireProcessTreeExtinction: true,
      maxOutputBytes: { stdout: maxBuffer, stderr: 16_384 },
      outputCapture: 'head',
      terminateOnOutputLimit: true,
    });
  } catch { throw new Error('DWS_WRITE_COMMAND_FAILED'); }
  if (!result || result.code !== 0 || result.killed || result.outputLimitExceeded
    || (result.stdoutTruncatedBytes ?? 0) > 0 || typeof result.stdout !== 'string') {
    throw new Error('DWS_WRITE_COMMAND_FAILED');
  }
  let response;
  try { response = JSON.parse(result.stdout); }
  catch { throw new Error('DWS_WRITE_RETURNED_INVALID_JSON'); }
  if (!response || response.success !== true) throw new Error('DWS_WRITE_UNVERIFIED');
  return response;
}

function assertIso(value, name) {
  if (!valid(value) || !Number.isFinite(Date.parse(value))) throw new Error(`${name}_INVALID`);
}

/** Fixed, write-only DWS commands. Every write requires a subsequent caller readback. */
export function createDwsCliWriter({ dwsCliEntry, profile, commandImpl = hostCommand, executable = process.execPath,
  timeoutMs = 15000, maxBuffer = 1_048_576 }) {
  if (!valid(dwsCliEntry) || !isAbsolute(dwsCliEntry) || !valid(profile) || !valid(executable)) {
    throw new Error('DWS_CLI_ENTRY_AND_PROFILE_REQUIRED');
  }
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1000 || timeoutMs > 30000
    || !Number.isSafeInteger(maxBuffer) || maxBuffer < 4096 || maxBuffer > 4_194_304) {
    throw new Error('DWS_WRITE_LIMITS_INVALID');
  }
  const run = args => runJson(commandImpl, executable, dwsCliEntry, [...args, '--profile', profile, '--format', 'json', '--yes'], timeoutMs, maxBuffer);
  return Object.freeze({
    updateDocumentDecision({ documentId, oldText, newText }) {
      if (!valid(documentId) || !valid(oldText) || typeof newText !== 'string' || newText.length > 32768) throw new Error('DWS_DOCUMENT_DECISION_INPUT_INVALID');
      return run(['doc', '+update', '--node', documentId, '--command', 'str_replace', '--old', oldText, '--new', newText]);
    },
    rescheduleEvent({ eventId, start, end }) {
      if (!valid(eventId)) throw new Error('DWS_EVENT_ID_REQUIRED');
      assertIso(start, 'DWS_EVENT_START'); assertIso(end, 'DWS_EVENT_END');
      if (Date.parse(start) >= Date.parse(end)) throw new Error('DWS_EVENT_RANGE_INVALID');
      return run(['calendar', '+reschedule', '--event', eventId, '--start', start, '--end', end]);
    },
    cancelEvent({ eventId }) {
      if (!valid(eventId)) throw new Error('DWS_EVENT_ID_REQUIRED');
      return run(['calendar', 'event', 'delete', '--id', eventId]);
    },
    readDocument({ documentId }) {
      if (!valid(documentId)) throw new Error('DWS_DOCUMENT_ID_REQUIRED');
      return run(['doc', '+fetch', '--node', documentId]);
    },
    readEvent({ eventId }) {
      if (!valid(eventId)) throw new Error('DWS_EVENT_ID_REQUIRED');
      return run(['calendar', 'event', 'get', '--id', eventId]);
    },
  });
}
