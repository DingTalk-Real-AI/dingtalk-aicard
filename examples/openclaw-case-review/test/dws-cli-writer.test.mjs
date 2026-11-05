import test from 'node:test';
import assert from 'node:assert/strict';
import { createDwsCliWriter } from '../integration/dws-cli-writer.mjs';

function fakeExecutor(outputs) {
  const calls = [];
  const executor = async (argv, options) => {
    calls.push({ argv, options });
    const next = outputs.shift();
    if (next?.error) throw next.error;
    return { code: next?.code ?? 0, stdout: next?.stdout ?? '{"success":true}', stderr: '', killed: false,
      outputLimitExceeded: false, stdoutTruncatedBytes: 0 };
  };
  return { executor, calls };
}

test('DWS writer uses fixed commands, profile, explicit confirmation, and readback commands', async () => {
  const fake = fakeExecutor([
    { stdout: '{"success":true}' }, { stdout: '{"success":true}' }, { stdout: '{"success":true}' },
    { stdout: '{"success":true}' }, { stdout: '{"success":true}' },
  ]);
  const writer = createDwsCliWriter({ dwsCliEntry: '/opt/dws/bin/dws.js', profile: 'corp:user', commandImpl: fake.executor, executable: '/usr/bin/node' });
  await writer.updateDocumentDecision({ documentId: 'doc-1', oldText: '- [ ] 开', newText: '- [x] 开' });
  await writer.rescheduleEvent({ eventId: 'event-1', start: '2026-11-26T10:30:00+08:00', end: '2026-11-26T11:30:00+08:00' });
  await writer.cancelEvent({ eventId: 'event-1' });
  await writer.readDocument({ documentId: 'doc-1' });
  await writer.readEvent({ eventId: 'event-1' });
  assert.deepEqual(fake.calls[0].argv, ['/usr/bin/node', '/opt/dws/bin/dws.js', 'doc', '+update', '--node', 'doc-1', '--command', 'str_replace', '--old', '- [ ] 开', '--new', '- [x] 开', '--profile', 'corp:user', '--format', 'json', '--yes']);
  assert.ok(fake.calls[1].argv.includes('calendar', '+reschedule', '--event', 'event-1'));
  assert.ok(fake.calls[2].argv.includes('calendar', 'event', 'delete', '--id', 'event-1'));
  assert.equal(fake.calls[0].argv.at(-1), '--yes');
  assert.equal(fake.calls[1].argv.at(-1), '--yes');
  assert.equal(fake.calls[2].argv.at(-1), '--yes');
  assert.equal(fake.calls[3].argv.at(-1), 'json');
  assert.equal(fake.calls[4].argv.at(-1), 'json');
  assert.equal(fake.calls.every(call => call.options.killProcessTree && call.options.requireProcessTreeExtinction), true);
});

test('DWS writer fails closed on non-success or invalid input', async () => {
  const fake = fakeExecutor([{ stdout: '{"success":false}' }]);
  const writer = createDwsCliWriter({ dwsCliEntry: '/opt/dws/bin/dws.js', profile: 'corp:user', commandImpl: fake.executor });
  await assert.rejects(writer.cancelEvent({ eventId: 'event-1' }), /DWS_WRITE_UNVERIFIED/);
  assert.throws(() => writer.rescheduleEvent({ eventId: 'event-1', start: 'bad', end: 'bad' }), /DWS_EVENT_START_INVALID/);
  assert.throws(() => writer.updateDocumentDecision({ documentId: 'doc-1', oldText: '', newText: 'x' }), /DWS_DOCUMENT_DECISION_INPUT_INVALID/);
});
