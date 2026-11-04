import { execFile } from 'node:child_process';
import { isAbsolute } from 'node:path';

const valid = value => typeof value === 'string' && value.trim().length > 0 && value.length <= 2048;
const object = value => !!value && typeof value === 'object' && !Array.isArray(value);

function runJson(execFileImpl, executable, entry, args, timeoutMs, maxBuffer) {
  return new Promise((resolve, reject) => {
    execFileImpl(executable, [entry, ...args], {
      encoding: 'utf8', timeout: timeoutMs, maxBuffer, windowsHide: true, shell: false,
    }, (error, stdout) => {
      if (error) return reject(new Error('DWS_READ_COMMAND_FAILED'));
      try { resolve(JSON.parse(stdout)); }
      catch { reject(new Error('DWS_READ_RETURNED_INVALID_JSON')); }
    });
  });
}

/** Run only fixed, read-only DWS CLI commands; no shell, retries, or output logging. */
export function createDwsCliReaders({ dwsCliEntry, profile, execFileImpl = execFile, executable = process.execPath,
  timeoutMs = 15000, maxBuffer = 1_048_576 }) {
  if (!valid(dwsCliEntry) || !isAbsolute(dwsCliEntry) || !valid(profile) || !valid(executable)) {
    throw new Error('DWS_CLI_ENTRY_AND_PROFILE_REQUIRED');
  }
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1000 || timeoutMs > 30000
    || !Number.isSafeInteger(maxBuffer) || maxBuffer < 4096 || maxBuffer > 4_194_304) {
    throw new Error('DWS_READ_LIMITS_INVALID');
  }
  return Object.freeze({
    readDocument({ documentId }) {
      if (!valid(documentId)) throw new Error('DWS_DOCUMENT_ID_REQUIRED');
      return runJson(execFileImpl, executable, dwsCliEntry,
        ['doc', '+fetch', '--node', documentId, '--profile', profile, '--format', 'json'], timeoutMs, maxBuffer);
    },
    async listEvents({ start, end }) {
      if (!valid(start) || !valid(end) || !Number.isFinite(Date.parse(start)) || !Number.isFinite(Date.parse(end))
        || Date.parse(start) >= Date.parse(end)) throw new Error('DWS_EVENT_RANGE_INVALID');
      const events = [];
      const seenCursors = new Set();
      let cursor;
      for (let page = 0; page < 50; page++) {
        const args = ['calendar', 'event', 'list', '--start', start, '--end', end, '--profile', profile, '--limit', '100'];
        if (cursor) args.push('--cursor', cursor);
        args.push('--format', 'json');
        const response = await runJson(execFileImpl, executable, dwsCliEntry, args, timeoutMs, maxBuffer);
        if (response?.success !== true) throw new Error('DWS_CALENDAR_READ_UNVERIFIED');
        const body = object(response.result) ? response.result : response;
        if (!Array.isArray(body.events)) throw new Error('DWS_CALENDAR_READ_INVALID');
        events.push(...body.events);
        const next = valid(body.nextCursor) ? body.nextCursor : undefined;
        if (next) {
          if (seenCursors.has(next)) throw new Error('DWS_CALENDAR_CURSOR_REPEATED');
          seenCursors.add(next);
          cursor = next;
          continue;
        }
        if (body.hasMore === true) throw new Error('DWS_CALENDAR_CURSOR_MISSING');
        return { ...response, result: { ...body, events, hasMore: false } };
      }
      throw new Error('DWS_CALENDAR_PAGE_LIMIT_REACHED');
    },
  });
}
