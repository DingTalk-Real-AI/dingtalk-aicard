import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync,rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { SandboxWorkflow } from '../src/sandbox.mjs';
import { buildCard,statusDelta,confirmationDelta,backDelta,resultDelta } from '../src/card.mjs';
const clock=Date.parse('2026-11-04T13:10:00+08:00');
const fixture=(path=':memory:')=>{
  let time=clock;
  const workflow=new SandboxWorkflow(path,{now:()=>time});
  const principal={authenticated:true,source:'offline-test-harness',actor:'tester',corpId:'test-corp',botId:'test-bot',conversationId:'test-dm'};
  const card=workflow.register({...principal,cycle:'2026-11',start:'2026-11-25T10:30:00+08:00',end:'2026-11-25T11:30:00+08:00'});
  const form={decision:['open'],start:'2026-11-26T10:30:00+08:00',end:'2026-11-26T11:30:00+08:00'};
  return {workflow,principal,card,form,setTime:v=>time=v};
};
const rejected=(fn,code)=>assert.throws(fn,e=>e.code===code);
test('propose never writes decision or calendar; confirm requires explicit consent',()=>{
  const {workflow:w,principal:p,card:c,form}=fixture();
  const q=w.propose(c.id,form,p,c.revision);
  assert.deepEqual(w.row('cards',c.id),c);
  rejected(()=>w.confirm(c.id,q.confirmationId,p),'EXPLICIT_CONFIRMATION_REQUIRED'); w.close();
});
test('confirmed open decision is mutually exclusive and calendar is updated in sandbox only',()=>{
  const {workflow:w,principal:p,card:c,form}=fixture();const q=w.propose(c.id,form,p,1);
  const r=w.confirm(c.id,q.confirmationId,p,{confirmed:true});
  assert.equal(r.state,'completed');assert.equal(r.mode,'sandbox');
  const next=w.row('cards',c.id);assert.deepEqual(next.document,{open:true,closed:false,datetime:form.start});assert.equal(next.meeting.start,form.start);w.close();
});
test('cancel only changes matching sandbox meeting, not initial screening',()=>{
  const {workflow:w,principal:p,card:c}=fixture();const q=w.propose(c.id,{decision:['closed']},p,1);
  assert.equal(w.confirm(c.id,q.confirmationId,p,{confirmed:true}).state,'completed');
  assert.equal(w.row('cards',c.id).meeting.status,'cancelled');assert.deepEqual(w.row('cards',c.id).document,{open:false,closed:true,datetime:''});w.close();
});
for(const key of ['actor','corpId','botId','conversationId'])test(`forged ${key} rejected`,()=>{
  const {workflow:w,principal:p,card:c,form}=fixture();rejected(()=>w.propose(c.id,form,{...p,[key]:'stranger'},1),'FORBIDDEN');w.close();
});
test('unverified callback source rejected; action context cannot authorize',()=>{
  const {workflow:w,principal:p,card:c,form}=fixture();for(const source of ['http','a2ui-action-context',undefined])rejected(()=>w.propose(c.id,form,{...p,source},1),'UNVERIFIED_TRANSPORT');w.close();
});
test('invalid decisions, dates, past dates, and wrong cycles rejected',()=>{
  const {workflow:w,principal:p,card:c,form}=fixture();
  for(const [patch,code] of [[{decision:[]},'DECISION_REQUIRED'],[{decision:['open','closed']},'DECISION_REQUIRED'],[{decision:['other']},'INVALID_DECISION'],[{start:'2026-11-31T10:30:00+08:00'},'INVALID_DATE'],[{start:'2026-11-01T10:30:00+08:00'},'INVALID_TIME_RANGE'],[{end:form.start},'INVALID_TIME_RANGE'],[{start:'2026-12-01T10:30:00+08:00',end:'2026-12-01T11:30:00+08:00'},'WRONG_CYCLE'],[{start:'2026-11-26T10:30:00Z'},'INVALID_DATE']])rejected(()=>w.propose(c.id,{...form,...patch},p,1),code);w.close();
});
test('expired cards and confirmations rejected',()=>{
  const {workflow:w,principal:p,card:c,form,setTime}=fixture();const q=w.propose(c.id,form,p,1);
  setTime(clock+300001);rejected(()=>w.confirm(c.id,q.confirmationId,p,{confirmed:true}),'CONFIRMATION_EXPIRED');
  setTime(c.expires);rejected(()=>w.propose(c.id,form,p,1),'EXPIRED');w.close();
});
test('repeated confirmation is idempotent; conflicting second proposal is stale',()=>{
  const {workflow:w,principal:p,card:c,form}=fixture();const first=w.propose(c.id,form,p,1),second=w.propose(c.id,{decision:['closed']},p,1);
  const r=w.confirm(c.id,first.confirmationId,p,{confirmed:true});assert.deepEqual(w.confirm(c.id,first.confirmationId,p,{confirmed:true}),r);
  rejected(()=>w.confirm(c.id,second.confirmationId,p,{confirmed:true}),'STALE_CARD');assert.equal(w.db.prepare("SELECT COUNT(*) AS n FROM audit WHERE action='confirm'").get().n,1);w.close();
});
for(const failure of ['document','calendar','unknown','card'])test(`failure state: ${failure}`,()=>{
  const {workflow:w,principal:p,card:c,form}=fixture();const q=w.propose(c.id,form,p,1);const r=w.confirm(c.id,q.confirmationId,p,{confirmed:true,simulateFailure:failure});
  assert.equal(r.state,({document:'failed',calendar:'partial',unknown:'unknown',card:'completed'})[failure]);
  if(failure!=='card')assert.equal(w.row('cards',c.id).meeting.start,c.meeting.start);
  if(['calendar','unknown'].includes(failure))rejected(()=>w.propose(c.id,form,p,2),'RECONCILIATION_REQUIRED');w.close();
});
test('receipts persist across restart and retries do not replay',()=>{
  const dir=mkdtempSync(join(tmpdir(),'case-card-'));try{
    const path=join(dir,'test.db');const {workflow:w,principal:p,card:c,form}=fixture(path);
    const q=w.propose(c.id,form,p,1),r=w.confirm(c.id,q.confirmationId,p,{confirmed:true});w.close();
    const reopened=new SandboxWorkflow(path,{now:()=>clock});assert.deepEqual(reopened.confirm(c.id,q.confirmationId,p,{confirmed:true}),r);reopened.close();
  }finally{rmSync(dir,{recursive:true,force:true});}
});
test('confirmation from another card cannot be applied',()=>{
  const {workflow:w,principal:p,card:c,form}=fixture();const q=w.propose(c.id,form,p,1);const other=w.register({...p,cycle:'2026-11',start:c.meeting.start,end:c.meeting.end});
  rejected(()=>w.confirm(other.id,q.confirmationId,p,{confirmed:true}),'CONFIRMATION_NOT_FOUND');w.close();
});
test('A2UI has bound inputs, safe defaults and disabled unconnected submission; delta preserves input',()=>{
  const {workflow:w,card:c}=fixture();const m=buildCard(c);const components=m[2].updateComponents.components;
  assert.deepEqual(m[1].updateDataModel.value.form.decision,[]);assert.equal(components.find(x=>x.id==='submit').disabled,true);
  assert.match(components.find(x=>x.id==='progress').content,/未核实：尚未读取主文档/);
  assert.doesNotMatch(components.find(x=>x.id==='progress').content,/20%|1\/5/);
  assert.equal(buildCard(c,{actionsEnabled:true})[2].updateComponents.components.find(x=>x.id==='submit').disabled,false);
  const delta=statusDelta(m,'still not connected');assert.equal(delta.length,1);assert.equal(delta[0].updateComponents.components.length,1);assert.equal('updateDataModel' in delta[0],false);
  assert.throws(()=>buildCard({...c,mode:'production'}),/PRODUCTION_NOT_IMPLEMENTED/);w.close();
});
test('confirmation and return deltas never reset input values; unknown is not displayed as success',()=>{
  const {workflow:w,principal:p,card:c}=fixture();const m=buildCard(c,{actionsEnabled:true});
  const q=w.propose(c.id,{decision:['closed']},p,1);
  const confirmation=confirmationDelta(m,c,q,{actionsEnabled:true}),back=backDelta(m);
  for(const delta of [confirmation,back])assert.equal(delta.some(d=>d.updateDataModel||d.createSurface),false);
  assert.equal(confirmation[0].updateComponents.components.find(c=>c.id==='confirm-button').action.event.context.confirmationId,q.confirmationId);
  assert.equal(confirmation[0].updateComponents.components.find(c=>c.id==='confirm-button').disabled,false);
  const original=m[2].updateComponents.components.map(c=>c.id).sort();
  assert.deepEqual(back[0].updateComponents.components.map(c=>c.id).sort(),original);
  const r=w.confirm(c.id,q.confirmationId,p,{confirmed:true,simulateFailure:'unknown'});
  const result=resultDelta(m,r);assert.match(result[0].updateComponents.components.find(c=>c.id==='title').text,/未完成/);w.close();
});
test('report rendering accepts only supplied facts and clearly preserves missing fields as unverified',()=>{
  const {workflow:w,card:c}=fixture();
  const report={stage:'月度建档完成，会议待召开',meetingMonth:'2026年11月',targetReleaseMonth:'2026年12月',
    candidates:['Aurora search flow'],risks:['文档和日历的参会人信息需核对'],attendeeStatus:'文档待确认；日历显示一位参会人已接受',manualDecision:'未裁决：两个复选框均未勾选',
    documentLink:'主文档已核实'};
  const components=buildCard({...c,report})[2].updateComponents.components;
  const text=components.map(c=>c.content??c.text??'').join('\n');
  assert.match(text,/月度建档完成/);assert.match(text,/Aurora search flow/);assert.match(text,/未裁决/);
  assert.match(text,/文档待确认；日历显示一位参会人已接受/);
  const withoutAttendeeStatus=buildCard({...c,report:{...report,attendeeStatus:undefined}})[2].updateComponents.components;
  assert.match(withoutAttendeeStatus.find(x=>x.id==='meetings').content,/未核实：尚未交叉核对文档与日历/);
  assert.doesNotMatch(text,/20%|已决定召开/);w.close();
});
