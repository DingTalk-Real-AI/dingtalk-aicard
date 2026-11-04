import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { BusinessA2UITest } from '../plugin/business-test.mjs';

const policy = Object.freeze({ accountId:'main', senderId:'tester', sessionKey:'dingtalk:direct:tester', robotCode:'bot-test' });
const clock = Date.parse('2026-11-05T09:00:00+08:00');

function setup() {
  const directory = mkdtempSync(join(tmpdir(), 'case-business-card-'));
  const context = { ...policy, isDirect:true, isCurrent:()=>true };
  const updates=[];
  const bridge={
    async createCurrentCard(request) { this.created=request; return {status:'accepted',cardInstanceId:'instance-test'}; },
    async updateCard(request) { updates.push(request); return {status:'accepted'}; },
  };
  const report={stage:'月度建档完成，会议待召开',meetingMonth:'2026年11月',targetReleaseMonth:'2026年12月',
    manualDecision:'未裁决：开/不开均未勾选',fallbackStart:'2026-11-25T10:30:00+08:00',fallbackEnd:'2026-11-25T11:30:00+08:00'};
  const adapter=new BusinessA2UITest({databasePath:join(directory,'cards.sqlite'),bridge,policy,reportProvider:async()=>report,reportOptions:{source:'test'},now:()=>clock});
  return {adapter,bridge,context,updates,directory,report};
}

function callback(outTrackId, context, name, actionContext, messageId) {
  return {trustedContext:{accountId:context.accountId,senderId:context.senderId,sessionKey:context.sessionKey,
    conversationId:'dm-test',robotCode:context.robotCode,isDirect:true},cardInstanceId:'instance-test',messageId,
    data:{outTrackId,userId:context.senderId,content:JSON.stringify({actionData:{a2uiAction:{name,context:actionContext}}})}};
}

test('interactive business form proposes, returns, rejects stale confirmation, then confirms in sandbox on the same card', async()=>{
  const h=setup();
  try {
    const created=await h.adapter.create(h.context,'tool-call-1');
    assert.equal(created.status,'accepted');
    assert.equal(h.bridge.created.messages[2].updateComponents.components.find(c=>c.id==='submit').disabled,false);
    assert.equal(h.bridge.created.messages[2].updateComponents.components.find(c=>c.id==='progress').content.includes('未裁决：开/不开均未勾选'),false);
    const row=h.adapter.transport.byInstance('instance-test');
    const card=h.adapter.cards.get(row.outTrackId);
    const form={decision:['open'],start:'2026-11-26T10:30:00+08:00',end:'2026-11-26T11:30:00+08:00'};
    const actionContext={cardId:card.cardId,nonce:card.nonce,revision:card.revision,form};
    const proposal=await h.adapter.handleAction(callback(row.outTrackId,h.context,'project_review.propose',actionContext,'msg-propose-1'));
    assert.equal(proposal.status,'proposal_ready');
    assert.equal(h.updates.at(-1).cardInstanceId,'instance-test');
    const confirmDelta=h.updates.at(-1).messages[0].updateComponents.components;
    const oldConfirmation=confirmDelta.find(c=>c.id==='confirm-button').action.event.context.confirmationId;
    assert.match(confirmDelta.find(c=>c.id==='confirm-summary').content,/2026-11-26/);
    const duplicate=await h.adapter.handleAction(callback(row.outTrackId,h.context,'project_review.propose',actionContext,'msg-propose-1'));
    assert.equal(duplicate.reason,'DUPLICATE_CALLBACK');
    const back=await h.adapter.handleAction(callback(row.outTrackId,h.context,'project_review.back',{cardId:card.cardId,nonce:card.nonce,revision:card.revision},'msg-back'));
    assert.equal(back.status,'returned');
    const stale=await h.adapter.handleAction(callback(row.outTrackId,h.context,'project_review.confirm',{cardId:card.cardId,nonce:card.nonce,revision:card.revision,confirmationId:oldConfirmation},'msg-stale-confirm'));
    assert.equal(stale.reason,'CONFIRMATION_REQUIRED_OR_STALE');
    const closedForm={decision:['closed']};
    const proposal2=await h.adapter.handleAction(callback(row.outTrackId,h.context,'project_review.propose',{...actionContext,form:closedForm},'msg-propose-2'));
    assert.equal(proposal2.status,'proposal_ready');
    const confirmationId=h.updates.at(-1).messages[0].updateComponents.components.find(c=>c.id==='confirm-button').action.event.context.confirmationId;
    const result=await h.adapter.handleAction(callback(row.outTrackId,h.context,'project_review.confirm',{cardId:card.cardId,nonce:card.nonce,revision:card.revision,confirmationId},'msg-confirm'));
    assert.equal(result.business,'completed');
    assert.equal(h.updates.at(-1).cardInstanceId,'instance-test');
    assert.equal(h.updates.at(-1).status,'FINISH');
    assert.equal(h.adapter.business.row('cards',card.cardId).meeting.status,'cancelled');
    assert.equal(h.adapter.cards.has(row.outTrackId),false);
  } finally { h.adapter.close(); rmSync(h.directory,{recursive:true,force:true}); }
});

test('unverified report time, forged callback identity and invalid actions fail closed', async()=>{
  const h=setup();
  try {
    h.report.fallbackStart='';
    assert.equal((await h.adapter.create(h.context,'tool-call-1')).reason,'REPORT_MEETING_NOT_VERIFIED');
    h.report.fallbackStart='2026-11-25T10:30:00+08:00';
    const created=await h.adapter.create(h.context,'tool-call-2');
    const row=h.adapter.transport.byInstance('instance-test');
    const card=h.adapter.cards.get(row.outTrackId);
    const event=callback(row.outTrackId,h.context,'project_review.propose',{cardId:card.cardId,nonce:card.nonce,revision:card.revision,
      form:{decision:['closed']}},'msg-forged');
    event.trustedContext.senderId='attacker';
    assert.equal((await h.adapter.handleAction(event)).reason,'TRUSTED_CALLBACK_CONTEXT_REQUIRED');
    const invalid=await h.adapter.handleAction(callback(row.outTrackId,h.context,'delete.everything',{cardId:card.cardId,nonce:card.nonce,revision:card.revision},'msg-invalid'));
    assert.equal(invalid.reason,'ACTION_NOT_ALLOWED');
    assert.equal(created.cardInstanceId,'instance-test');
    assert.equal(h.updates.length,0);
  } finally { h.adapter.close(); rmSync(h.directory,{recursive:true,force:true}); }
});
