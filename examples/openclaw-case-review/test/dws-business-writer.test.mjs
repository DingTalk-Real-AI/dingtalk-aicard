import test from 'node:test';
import assert from 'node:assert/strict';
import { applyDwsDecision } from '../integration/dws-business-writer.mjs';

const base = {
  documentId: 'doc-1', fallbackEventId: 'event-1',
  fallbackStart: '2026-11-25T10:30:00+08:00', fallbackEnd: '2026-11-25T11:30:00+08:00',
  decisionControl: '- [ ] 开\n- [ ] 不开',
};

function writerFor({ document = '- [x] 开\n- [ ] 不开', event = { status: 'confirmed', start: { dateTime: '2026-11-26T10:30:00+08:00' }, end: { dateTime: '2026-11-26T11:30:00+08:00' } }, fail = '' } = {}) {
  const calls = [];
  return {
    calls,
    async updateDocumentDecision(args) { calls.push(['document-write', args]); if (fail === 'document') throw new Error('document-failed'); return { success: true }; },
    async readDocument() { calls.push(['document-read']); return { success: true, complete: true, content: { markdown: document } }; },
    async rescheduleEvent(args) { calls.push(['calendar-reschedule', args]); if (fail === 'calendar') throw new Error('calendar-failed'); return { success: true }; },
    async cancelEvent(args) { calls.push(['calendar-cancel', args]); if (fail === 'calendar') throw new Error('calendar-failed'); return { success: true }; },
    async readEvent() { calls.push(['calendar-read']); return { success: true, result: event }; },
  };
}

test('DWS decision writer performs fresh-state check, document write/readback, then calendar write/readback', async () => {
  const writer = writerFor();
  const result = await applyDwsDecision({ writer, report: base, change: { decision: 'open', start: '2026-11-26T10:30:00+08:00', end: '2026-11-26T11:30:00+08:00' }, freshReport: async () => base });
  assert.equal(result.state, 'completed');
  assert.deepEqual(writer.calls.map(call => call[0]), ['document-write', 'document-read', 'calendar-reschedule', 'calendar-read']);
  assert.deepEqual(writer.calls[0][1], { documentId: 'doc-1', oldText: '- [ ] 开\n- [ ] 不开', newText: '- [x] 开\n- [ ] 不开' });
});

test('DWS decision writer stops before calendar when document write/readback fails', async () => {
  const failed = writerFor({ fail: 'document' });
  await assert.rejects(applyDwsDecision({ writer: failed, report: base, change: { decision: 'closed' }, freshReport: async () => base }), /document-failed/);
  assert.deepEqual(failed.calls.map(call => call[0]), ['document-write']);
  const mismatch = writerFor({ document: '- [ ] 开\n- [ ] 不开' });
  await assert.rejects(applyDwsDecision({ writer: mismatch, report: base, change: { decision: 'open', start: '2026-11-26T10:30:00+08:00', end: '2026-11-26T11:30:00+08:00' }, freshReport: async () => base }), /DWS_DOCUMENT_DECISION_READBACK_MISMATCH/);
  assert.deepEqual(mismatch.calls.map(call => call[0]), ['document-write', 'document-read']);
});

test('DWS decision writer reports partial completion after calendar failure and never retries', async () => {
  const writer = writerFor({ fail: 'calendar' });
  const result = await applyDwsDecision({ writer, report: base, change: { decision: 'open', start: '2026-11-26T10:30:00+08:00', end: '2026-11-26T11:30:00+08:00' }, freshReport: async () => base });
  assert.equal(result.state, 'partial');
  assert.equal(result.document, 'verified');
  assert.equal(result.calendar, 'failed');
  assert.deepEqual(writer.calls.map(call => call[0]), ['document-write', 'document-read', 'calendar-reschedule']);
});

test('DWS decision writer refuses stale report state before any write', async () => {
  const writer = writerFor();
  const changed = { ...base, fallbackStart: '2026-11-25T11:30:00+08:00' };
  await assert.rejects(applyDwsDecision({ writer, report: base, change: { decision: 'closed' }, freshReport: async () => changed }), /DWS_STATE_CHANGED/);
  assert.equal(writer.calls.length, 0);
});
