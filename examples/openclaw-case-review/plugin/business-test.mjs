import { randomUUID, timingSafeEqual } from 'node:crypto';
import { CardStore } from '../integration/store.mjs';
import { createPrivateTestAuthorizer } from '../integration/authorization.mjs';
import { parseCallback } from '../integration/callback.mjs';
import { buildCard, confirmationDelta, backDelta, resultDelta } from '../src/card.mjs';
import { SandboxWorkflow, Rejected } from '../src/sandbox.mjs';

const ACTIONS = new Set(['project_review.propose', 'project_review.confirm', 'project_review.back']);
const valid = value => typeof value === 'string' && value.length > 0 && value.length <= 512
  && !/[\s\x00-\x1f]/u.test(value);
const record = value => !!value && typeof value === 'object' && !Array.isArray(value);

function principal(context) {
  return {
    authenticated: true,
    source: 'connector-stream',
    actor: context.senderId,
    corpId: context.accountId,
    botId: context.robotCode,
    conversationId: context.sessionKey,
  };
}

function formValue(value) {
  if (!record(value)) return value;
  if (Object.keys(value).length === 1 && 'path' in value) return undefined;
  return value;
}

function nonceMatches(value, expected) {
  if (typeof value !== 'string' || !/^[a-f0-9]{32}$/u.test(value) || value.length !== expected.length) return false;
  return timingSafeEqual(Buffer.from(value), Buffer.from(expected));
}

export class BusinessA2UITest {
  constructor({ databasePath, bridge, policy, reportProvider, decisionApplier, now = Date.now, ttlMs = 300000 }) {
    if (!databasePath || !bridge || !policy) throw new Error('EXPLICIT_BUSINESS_TEST_DEPENDENCIES_REQUIRED');
    if (reportProvider !== undefined && typeof reportProvider !== 'function') throw new Error('INVALID_REPORT_PROVIDER');
    if (decisionApplier !== undefined && typeof decisionApplier !== 'function') throw new Error('INVALID_DECISION_APPLIER');
    if (ttlMs < 10000 || ttlMs > 1800000) throw new Error('INVALID_LIMITS');
    this.transport = new CardStore(databasePath);
    this.business = new SandboxWorkflow(`${databasePath}.business`, { now });
    this.bridge = bridge;
    this.authorize = createPrivateTestAuthorizer(policy);
    this.policy = policy;
    this.reportProvider = reportProvider;
    this.decisionApplier = decisionApplier;
    this.now = now;
    this.ttlMs = ttlMs;
    this.cards = new Map();
  }

  close() { this.unsubscribe?.(); this.cards.clear(); this.transport.close(); this.business.close(); }
  isAuthorized(context) { return this.authorize(context); }
  isAuthorizedCallback(context) {
    return context?.accountId === this.policy.accountId && context?.senderId === this.policy.senderId
      && context?.sessionKey === this.policy.sessionKey && context?.isDirect === true
      && context?.robotCode === this.policy.robotCode;
  }

  async create(context, callId) {
    if (!valid(context?.sessionKey) || !valid(callId) || !this.isAuthorized(context)) {
      return { status: 'rejected', reason: 'PRIVATE_TEST_SCOPE_NOT_AUTHORIZED' };
    }
    const previous = this.transport.byRequest(callId);
    if (previous) return { status: previous.lastOutcome ?? 'unknown', outTrackId: previous.outTrackId, reused: true, readback: 'unverified', rendering: 'unverified' };
    let report;
    try { report = await this.reportProvider(context); } catch { /* fail closed below */ }
    if (!record(report)) return { status: 'rejected', reason: 'REPORT_SOURCE_UNAVAILABLE' };
    const start = report.fallbackStart;
    const end = report.fallbackEnd;
    if (!valid(start) || !valid(end)) return { status: 'rejected', reason: 'REPORT_MEETING_NOT_VERIFIED' };
    const p = principal(context);
    const cardId = randomUUID();
    const row = {
      outTrackId: `case_a2ui_business_${randomUUID()}`,
      surfaceId: `case-highlight-${cardId}`,
      requestKey: callId,
      accountId: context.accountId,
      senderId: context.senderId,
      sessionKey: context.sessionKey,
      expiresAt: this.now() + this.ttlMs,
      nonce: randomUUID().replaceAll('-', ''),
      revision: 1,
      updateId: randomUUID(),
    };
    const reserved = this.transport.reserve(row, 100);
    if (reserved.status !== 'reserved') return { status: 'rejected', reason: reserved.status };
    let card;
    try {
      card = this.business.register({ id: cardId, ...p, cycle: start.slice(0, 7), start, end, ttlMs: this.ttlMs });
    } catch (error) {
      this.transport.outcome(row.outTrackId, 'creating', 'failed', { status: 'failed', receipt: {} });
      return { status: 'rejected', reason: error?.code ?? 'BUSINESS_CARD_REGISTRATION_FAILED' };
    }
    const messages = buildCard({ ...card, nonce: row.nonce, report }, { actionsEnabled: true });
    const sent = await this.bridge.createCurrentCard({
      context, outTrackId: row.outTrackId, summary: '案例亮点汇报卡（私聊沙盒）', messages,
    }).catch(() => ({ status: 'unknown' }));
    if (sent?.status !== 'accepted' || !valid(sent.cardInstanceId)) {
      this.transport.outcome(row.outTrackId, 'creating', sent?.status === 'failed' ? 'failed' : 'unknown', { status: sent?.status ?? 'unknown' });
      return { status: sent?.status === 'failed' ? 'failed' : 'unknown', outTrackId: row.outTrackId };
    }
    this.transport.outcome(row.outTrackId, 'creating', 'accepted', { status: 'accepted', receipt: { cardInstanceId: sent.cardInstanceId } });
    if (!this.transport.bindInstance(sent.cardInstanceId, row.outTrackId)) return { status: 'unknown', reason: 'CARD_MAPPING_NOT_SAVED' };
    this.cards.set(row.outTrackId, { ...row, cardId: card.id, cardInstanceId: sent.cardInstanceId, messages, context, report });
    return { status: 'accepted', outTrackId: row.outTrackId, cardInstanceId: sent.cardInstanceId, readback: 'unverified', rendering: 'unverified', interaction: 'unverified' };
  }

  async handleAction(event) {
    const context = event?.trustedContext;
    const instance = event?.cardInstanceId;
    if (!context || !this.isAuthorizedCallback(context) || !valid(instance) || !valid(event?.messageId)) {
      return { status: 'rejected', reason: 'TRUSTED_CALLBACK_CONTEXT_REQUIRED' };
    }
    const row = this.transport.byInstance(instance);
    const live = row && this.cards.get(row.outTrackId);
    if (!row || row.state !== 'accepted' || !live || row.accountId !== context.accountId || row.senderId !== context.senderId
      || row.sessionKey !== context.sessionKey || row.expiresAt <= this.now()) return { status: 'rejected', reason: 'UNKNOWN_OR_STALE_CARD' };
    let parsed;
    try { parsed = parseCallback(event.data); } catch { return { status: 'rejected', reason: 'MALFORMED_CALLBACK' }; }
    const action = parsed.event;
    if (!action || !ACTIONS.has(action.name)) return { status: 'rejected', reason: 'ACTION_NOT_ALLOWED' };
    const body = record(action.context) ? action.context : {};
    if (body.cardId !== live.cardId || !nonceMatches(body.nonce, live.nonce) || body.revision !== live.revision) {
      return { status: 'rejected', reason: 'ACTION_OR_REVISION_MISMATCH' };
    }
    const claim = this.transport.claimCallback(event.messageId, row.outTrackId);
    if (claim.status !== 'claimed') return { status: 'rejected', reason: 'DUPLICATE_CALLBACK' };
    const p = principal(context);
    try {
      if (action.name === 'project_review.propose') {
        const rawForm = formValue(body.form);
        const proposal = this.business.propose(live.cardId, rawForm, p, live.revision);
        live.pendingConfirmation = proposal.confirmationId;
        const delta = confirmationDelta(live.messages, { id: live.cardId, nonce: live.nonce, revision: live.revision }, proposal, { actionsEnabled: true });
        const update = await this.bridge.updateCard({ cardInstanceId: instance, outTrackId: row.outTrackId, outUpdateId: randomUUID(),
          status: 'PROCESSING', messages: delta }).catch(() => ({ status: 'unknown' }));
        if (update.status !== 'accepted') {
          this.transport.finishCallback(event.messageId, 'unknown');
          return { status: update.status, sameCardUpdate: update.status };
        }
        live.messages = [...live.messages, ...delta];
        this.transport.finishCallback(event.messageId, 'completed');
        return { status: 'proposal_ready', confirmationId: proposal.confirmationId, sameCardUpdate: 'accepted', rendering: 'unverified' };
      }
      if (action.name === 'project_review.back') {
        live.pendingConfirmation = undefined;
        const delta = backDelta(live.messages);
        const update = await this.bridge.updateCard({ cardInstanceId: instance, outTrackId: row.outTrackId, outUpdateId: randomUUID(),
          status: 'PROCESSING', messages: delta }).catch(() => ({ status: 'unknown' }));
        if (update.status !== 'accepted') {
          this.transport.finishCallback(event.messageId, 'unknown');
          return { status: update.status, sameCardUpdate: update.status };
        }
        live.messages = [...live.messages, ...delta];
        this.transport.finishCallback(event.messageId, 'completed');
        return { status: 'returned', sameCardUpdate: 'accepted', rendering: 'unverified' };
      }
      const confirmationId = body.confirmationId;
      if (!valid(confirmationId) || confirmationId !== live.pendingConfirmation) {
        this.transport.finishCallback(event.messageId, 'rejected');
        return { status: 'rejected', reason: 'CONFIRMATION_REQUIRED_OR_STALE' };
      }
      let result;
      if (this.decisionApplier) {
        const pending = this.business.confirmation(live.cardId, confirmationId);
        if (!pending) {
          this.transport.finishCallback(event.messageId, 'rejected');
          return { status: 'rejected', reason: 'CONFIRMATION_NOT_FOUND' };
        }
        try {
          result = await this.decisionApplier({
            report: live.report,
            change: pending.change,
            freshReport: () => this.reportProvider(context),
          });
          if (result?.state === 'completed') {
            try { this.business.confirm(live.cardId, confirmationId, p, { confirmed: true }); }
            catch { result = { ...result, ledger: 'unknown', message: `${result.message} 本地审计回写待核对。` }; }
          }
        } catch (error) {
          result = { mode: 'dws', state: 'failed', document: 'unchanged', calendar: 'unchanged',
            message: `DWS 写入未完成：${error?.message ?? '未知错误'}` };
        }
      } else {
        result = this.business.confirm(live.cardId, confirmationId, p, { confirmed: true });
      }
      const delta = resultDelta(live.messages, result);
      const update = await this.bridge.updateCard({ cardInstanceId: instance, outTrackId: row.outTrackId, outUpdateId: randomUUID(),
        status: 'FINISH', messages: delta }).catch(() => ({ status: 'unknown' }));
      if (update.status !== 'accepted') {
        this.transport.finishCallback(event.messageId, 'unknown');
        return { status: update.status, business: result.state, sameCardUpdate: update.status };
      }
      this.transport.finishCallback(event.messageId, 'completed');
      this.transport.outcome(row.outTrackId, row.state, 'clicked', { status: 'clicked', receipt: {} });
      this.cards.delete(row.outTrackId);
      return { status: result.state, business: result.state, sameCardUpdate: 'accepted', rendering: 'unverified' };
    } catch (error) {
      if (error instanceof Rejected) {
        this.transport.finishCallback(event.messageId, 'rejected');
        return { status: 'rejected', reason: error.code };
      }
      this.transport.finishCallback(event.messageId, 'unknown');
      return { status: 'rejected', reason: 'BUSINESS_ACTION_FAILED' };
    }
  }
}
