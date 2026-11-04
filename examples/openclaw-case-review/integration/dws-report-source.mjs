const object = value => !!value && typeof value === 'object' && !Array.isArray(value);
const text = (value, fallback = '') => typeof value === 'string' && value.trim() ? value.trim() : fallback;

function requireFunction(value, name) {
  if (typeof value !== 'function') throw new Error(`${name}_REQUIRED`);
  return value;
}

function section(markdown, heading) {
  const source = text(markdown);
  const escaped = heading.replace(/[.*+?^${}()|[\]\\]/gu, '\\$&');
  const match = source.match(new RegExp(`(?:^|\\n)##\\s+(?:\\\\?\\d+\\\\?\\.\\s+)?${escaped}\\s*\\n([\\s\\S]*?)(?=\\n##\\s+|$)`, 'u'));
  return match?.[1] ?? '';
}

function meetingFromEvents(events, summaries, period, label) {
  const matches = events.filter(event => object(event)
    && summaries.some(summary => text(event.summary).includes(summary))
    && text(event.start?.dateTime).slice(0, 7) === period
    && text(event.end?.dateTime));
  if (matches.length !== 1) throw new Error(`${label}_MEETING_NOT_UNIQUE`);
  const event = matches[0];
  const attendees = Array.isArray(event.attendees) ? event.attendees : [];
  return {
    start: text(event.start.dateTime),
    end: text(event.end.dateTime),
    status: text(event.status, 'unknown'),
    eventId: text(event.id),
    attendeeNames: attendees.map(attendee => text(attendee.displayName)).filter(Boolean),
    acceptedAttendees: attendees.filter(attendee => text(attendee.responseStatus).toLowerCase() === 'accepted')
      .map(attendee => text(attendee.displayName)).filter(Boolean),
    organizerName: text(event.organizer?.displayName),
  };
}

function candidateRows(markdown) {
  const lines = text(markdown).split(/\r?\n/u);
  return lines.filter(line => /^\|\s*\d+\s*\|/u.test(line) && !/序号|------/u.test(line))
    .map(line => line.split('|').slice(1).map(value => value.trim()).filter(Boolean)[1])
    .filter(Boolean).slice(0, 12);
}

function decision(markdown) {
  const control = section(markdown, '兜底会控制');
  const open = /^\s*-\s*\[[xX]\]\s*开\s*$/mu.test(control);
  const closed = /^\s*-\s*\[[xX]\]\s*不开\s*$/mu.test(control);
  if (open && closed) return '冲突：开与不开同时勾选';
  if (open) return '开';
  if (closed) return '不开';
  return '未裁决：开/不开均未勾选';
}

function attendeeStatus(markdown, event) {
  const documentSaysUnconfirmed = /其他参会人名单尚未确认|其他参会人.*待确认/u.test(markdown);
  const accepted = event.acceptedAttendees.filter(name => name !== event.organizerName);
  if (documentSaysUnconfirmed && accepted.length) {
    return `文档写“其他参会人待确认”；日历显示已接受：${accepted.join('、')}`;
  }
  if (documentSaysUnconfirmed) return '文档写“其他参会人待确认”；日历未发现额外已接受参会人';
  if (accepted.length) return `日历显示已接受参会人：${accepted.join('、')}`;
  return '未核实：文档与日历未提供可比对的额外参会人信息';
}

/**
 * Build a report snapshot from injected DWS reads. The adapter performs no writes.
 * The caller supplies trusted identity and profile selection in the injected readers.
 */
export function createDwsReportSource({ readDocument, listEvents, documentId, periodStart, periodEnd, now = () => new Date().toISOString() }) {
  requireFunction(readDocument, 'READ_DOCUMENT');
  requireFunction(listEvents, 'LIST_EVENTS');
  if (!text(documentId) || !text(periodStart) || !text(periodEnd)) throw new Error('DWS_REPORT_RANGE_REQUIRED');
  return async function reportSource(context) {
    const [documentResponse, calendarResponse] = await Promise.all([
      readDocument({ context, documentId }),
      listEvents({ context, start: periodStart, end: periodEnd }),
    ]);
    if (!object(documentResponse) || documentResponse.success === false) throw new Error('DWS_DOCUMENT_READ_UNVERIFIED');
    const document = object(documentResponse.content) ? documentResponse.content : documentResponse;
    if (!object(calendarResponse) || calendarResponse.success === false) throw new Error('DWS_CALENDAR_READ_UNVERIFIED');
    const calendar = object(calendarResponse.result) ? calendarResponse.result : calendarResponse;
    if (!object(document) || !text(document.markdown)) throw new Error('DWS_DOCUMENT_READ_UNVERIFIED');
    if (!object(calendar) || calendar.hasMore === true || calendar.complete === false) {
      throw new Error('DWS_CALENDAR_READ_INCOMPLETE');
    }
    const events = Array.isArray(calendar.events) ? calendar.events : [];
    const markdown = document.markdown;
    const meetingMonth = (markdown.match(/会议月份：\s*([^\n]+)/u)?.[1] ?? '').trim();
    const period = /^\d{4}-\d{2}/u.test(meetingMonth) ? meetingMonth.slice(0, 7) : periodStart.slice(0, 7);
    const fallback = meetingFromEvents(events, ['兜底会'], period, 'FALLBACK');
    const initial = meetingFromEvents(events, ['案例亮点会', '初筛会'], period, 'INITIAL');
    if (fallback.status !== 'confirmed') throw new Error('FALLBACK_MEETING_NOT_CONFIRMED');
    const risksSection = section(markdown, '风险与待确认事项');
    const risks = risksSection.split(/\r?\n/u).map(line => line.replace(/^\s*[-*]\s*/u, '').trim())
      .filter(line => line && !/^实时日历已核实/u.test(line)).slice(0, 12);
    const report = {
      source: 'dws',
      verifiedAt: now(),
      documentId: text(document.nodeId, documentId),
      documentLink: text(document.docUrl),
      stage: markdown.match(/阶段：\s*([^\n]+)/u)?.[1]?.trim() ?? '',
      meetingMonth,
      targetReleaseMonth: markdown.match(/目标发布月份：\s*([^\n]+)/u)?.[1]?.trim() ?? '',
      candidates: candidateRows(markdown),
      risks,
      initialMeeting: `${initial.start}—${initial.end}`,
      fallbackStart: fallback.start,
      fallbackEnd: fallback.end,
      fallbackEventId: fallback.eventId,
      initialEventId: initial.eventId,
      attendeeStatus: attendeeStatus(markdown, fallback),
      manualDecision: decision(markdown),
    };
    if (!report.stage || !report.meetingMonth || !report.fallbackStart || !report.fallbackEnd) {
      throw new Error('DWS_REPORT_REQUIRED_FACT_MISSING');
    }
    return Object.freeze(report);
  };
}
