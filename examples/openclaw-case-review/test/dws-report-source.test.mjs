import test from 'node:test';
import assert from 'node:assert/strict';
import { createDwsReportSource } from '../integration/dws-report-source.mjs';

const markdown = `## 1\\. 当前状态

阶段：月度建档完成，会议待召开
会议月份：2026年11月
目标发布月份：2026年12月
已确认：案例亮点会安排在2026-11-10 10:30—11:30；兜底会安排在2026-11-25 10:30—11:30。

## 2\\. 候选案例池
| 序号 | 候选案例 | 统筹人 |
|------|----------|--------|
| 1 | 演示案例 | 甲 |

## 5\\. 风险与待确认事项
其他参会人名单尚未确认。

## 6\\. 兜底会控制
### 人工裁决
- [ ] 开
- [ ] 不开
`;

const events = [
  { id: 'initial-1', summary: '11月份案例亮点初筛会', status: 'confirmed', start: { dateTime: '2026-11-10T10:30:00+08:00' }, end: { dateTime: '2026-11-10T11:30:00+08:00' }, attendees: [{ displayName: '当前用户', responseStatus: 'accepted' }] },
  { id: 'fallback-1', summary: '兜底会', status: 'confirmed', start: { dateTime: '2026-11-25T10:30:00+08:00' }, end: { dateTime: '2026-11-25T11:30:00+08:00' }, organizer: { displayName: '当前用户' }, attendees: [{ displayName: '当前用户', responseStatus: 'accepted' }, { displayName: '已接受参会人', responseStatus: 'accepted' }] },
];

test('DWS report source returns verified document/calendar facts and highlights attendee drift', async () => {
  const eventReads=[];
  const source = createDwsReportSource({
    documentId: 'doc-1', periodStart: '2026-11-01T00:00:00+08:00', periodEnd: '2026-12-01T00:00:00+08:00',
    readDocument: async () => ({ success: true, complete: true, content: { nodeId: 'doc-1', docUrl: 'https://example.invalid/doc-1', markdown } }),
    listEvents: async ({start,end}) => {
      eventReads.push({start,end});
      return { success: true, result: { events: events.filter(event=>event.start.dateTime>=start&&event.start.dateTime<end), hasMore: false } };
    },
    now: () => '2026-11-05T09:00:00+08:00',
  });
  const report = await source({ senderName: '当前用户' });
  assert.equal(report.source, 'dws');
  assert.equal(report.fallbackStart, '2026-11-25T10:30:00+08:00');
  assert.equal(report.initialEventId, 'initial-1');
  assert.match(report.attendeeStatus, /文档写.*待确认/);
  assert.match(report.attendeeStatus, /已接受参会人/);
  assert.equal(report.manualDecision, '未裁决：开/不开均未勾选');
  assert.equal(report.decisionControl, '- [ ] 开\n- [ ] 不开');
  assert.deepEqual(eventReads,[
    {start:'2026-11-10T00:00:00+08:00',end:'2026-11-11T00:00:00+08:00'},
    {start:'2026-11-25T00:00:00+08:00',end:'2026-11-26T00:00:00+08:00'},
  ]);
});

test('DWS report source fails closed for ambiguous or unconfirmed fallback meetings', async () => {
  const source = createDwsReportSource({
    documentId: 'doc-1', periodStart: '2026-11-01T00:00:00+08:00', periodEnd: '2026-12-01T00:00:00+08:00',
    readDocument: async () => ({ success: true, content: { markdown } }),
    listEvents: async () => ({ success: true, result: { events: events.map(event => event.summary === '兜底会' ? { ...event, status: 'cancelled' } : event), hasMore: false } }),
  });
  await assert.rejects(source({}), /FALLBACK_MEETING_NOT_CONFIRMED/);
});

test('DWS report source refuses incomplete calendar pagination', async () => {
  const source = createDwsReportSource({
    documentId: 'doc-1', periodStart: '2026-11-01T00:00:00+08:00', periodEnd: '2026-12-01T00:00:00+08:00',
    readDocument: async () => ({ success: true, content: { markdown } }),
    listEvents: async () => ({ success: true, result: { events, hasMore: true } }),
  });
  await assert.rejects(source({}), /DWS_CALENDAR_READ_INCOMPLETE/);
});
