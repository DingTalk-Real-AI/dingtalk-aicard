const record = value => !!value && typeof value === 'object' && !Array.isArray(value);
const text = value => typeof value === 'string' && value.trim().length > 0;

function readMarkdown(response) {
  if (!record(response) || response.success !== true || response.complete === false) throw new Error('DWS_DOCUMENT_READBACK_UNVERIFIED');
  const document = record(response.content) ? response.content : response;
  if (!text(document.markdown)) throw new Error('DWS_DOCUMENT_READBACK_UNVERIFIED');
  return document.markdown;
}

function readEvent(response) {
  if (!record(response) || response.success !== true || response.complete === false) throw new Error('DWS_EVENT_READBACK_UNVERIFIED');
  const event = record(response.result) && record(response.result.event) ? response.result.event : response.result ?? response.event ?? response;
  if (!record(event)) throw new Error('DWS_EVENT_READBACK_UNVERIFIED');
  return event;
}

function decisionLines(markdown) {
  const lines = String(markdown).split(/\r?\n/u);
  const open = lines.find(line => /^\s*-\s*\[[ xX]\]\s*开\s*$/u.test(line));
  const closed = lines.find(line => /^\s*-\s*\[[ xX]\]\s*不开\s*$/u.test(line));
  if (!open || !closed) throw new Error('DWS_DECISION_CONTROL_NOT_UNIQUE');
  return { open, closed };
}

function checked(line, value) { return line.replace(/\[[ xX]\]/u, value ? '[x]' : '[ ]'); }

function assertEvent(event, change) {
  const status = String(event.status ?? '').toLowerCase();
  if (change.decision === 'closed') {
    if (!['cancelled', 'canceled', 'deleted'].includes(status)) throw new Error('DWS_EVENT_CANCEL_READBACK_MISMATCH');
    return;
  }
  const start = event.start?.dateTime ?? event.start;
  const end = event.end?.dateTime ?? event.end;
  if (start !== change.start || end !== change.end) throw new Error('DWS_EVENT_TIME_READBACK_MISMATCH');
}

/**
 * Apply one explicitly confirmed decision to DWS in strict order.
 * The caller supplies a fresh report read immediately before this function.
 * A document write is never retried, and calendar work is skipped if document
 * write or document readback fails.
 */
export async function applyDwsDecision({ writer, report, change, freshReport }) {
  if (!writer || typeof writer.updateDocumentDecision !== 'function' || typeof writer.readDocument !== 'function'
    || typeof writer.readEvent !== 'function') throw new Error('DWS_WRITER_REQUIRED');
  if (!record(report) || !record(change) || typeof freshReport !== 'function') throw new Error('DWS_FRESH_REPORT_REQUIRED');
  const current = await freshReport();
  if (!record(current) || current.documentId !== report.documentId || current.fallbackEventId !== report.fallbackEventId
    || current.fallbackStart !== report.fallbackStart || current.fallbackEnd !== report.fallbackEnd) {
    throw new Error('DWS_STATE_CHANGED');
  }
  const lines = decisionLines(current.decisionControl);
  const desiredOpen = change.decision === 'open';
  const oldText = `${lines.open}\n${lines.closed}`;
  const newText = `${checked(lines.open, desiredOpen)}\n${checked(lines.closed, !desiredOpen)}`;
  const documentWrite = await writer.updateDocumentDecision({ documentId: current.documentId, oldText, newText });
  const documentReadback = await writer.readDocument({ documentId: current.documentId });
  const markdown = readMarkdown(documentReadback);
  const updatedLines = decisionLines(markdown);
  if (updatedLines.open !== checked(lines.open, desiredOpen) || updatedLines.closed !== checked(lines.closed, !desiredOpen)) {
    throw new Error('DWS_DOCUMENT_DECISION_READBACK_MISMATCH');
  }
  try {
    let calendarWrite;
    if (desiredOpen) {
      calendarWrite = await writer.rescheduleEvent({ eventId: current.fallbackEventId, start: change.start, end: change.end });
    } else {
      calendarWrite = await writer.cancelEvent({ eventId: current.fallbackEventId });
    }
    const calendarReadback = await writer.readEvent({ eventId: current.fallbackEventId });
    const event = readEvent(calendarReadback);
    assertEvent(event, change);
    return { mode: 'dws', state: 'completed', document: 'verified', calendar: 'verified',
      documentWrite, documentReadback, calendarWrite, calendarReadback,
      message: '裁决和日程已写入并回读核验。' };
  } catch (error) {
    return { mode: 'dws', state: 'partial', document: 'verified', calendar: 'failed',
      message: `裁决已写入并回读；日程未完成或回读不一致：${error.message}` };
  }
}
