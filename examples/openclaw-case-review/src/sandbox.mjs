import { randomUUID, createHash, timingSafeEqual } from 'node:crypto';
import { DatabaseSync } from 'node:sqlite';

export class Rejected extends Error { constructor(code) { super(code); this.code=code; } }
const deny=code=>{throw new Rejected(code);};
const digest=value=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
function checkDate(value) {
  if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00\+08:00$/.test(value)) deny('INVALID_DATE');
  const ms=Date.parse(value);
  if(!Number.isFinite(ms)||new Date(ms+8*3600000).toISOString().slice(0,19)!==value.slice(0,19)) deny('INVALID_DATE');
  return ms;
}
export function normalizeForm(form, now, current) {
  if(!form || !Array.isArray(form.decision)||form.decision.length!==1) deny('DECISION_REQUIRED');
  const decision=form.decision[0];
  if(!['open','closed'].includes(decision)) deny('INVALID_DECISION');
  if(Date.parse(current.start)<=now||current.status!=='confirmed') deny('MEETING_NOT_EDITABLE');
  if(decision==='closed') return {decision};
  const start=checkDate(form.start), end=checkDate(form.end);
  if(start<=now || end<=start || end-start>8*3600000) deny('INVALID_TIME_RANGE');
  // Card only controls the registered meeting cycle, not arbitrary future events.
  if(form.start.slice(0,7)!==current.cycle || form.end.slice(0,7)!==current.cycle) deny('WRONG_CYCLE');
  return {decision,start:form.start,end:form.end};
}

/** Local sandbox only. No production DWS, outbound messaging or unauthenticated HTTP endpoint. */
export class SandboxWorkflow {
  constructor(path=':memory:', {now=()=>Date.now()}={}) {
    this.now=now; this.db=new DatabaseSync(path);
    this.db.exec(`PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;
      CREATE TABLE IF NOT EXISTS cards(id TEXT PRIMARY KEY, data TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS confirmations(id TEXT PRIMARY KEY, card TEXT NOT NULL, data TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, card TEXT NOT NULL, data TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at INTEGER, actor TEXT, card TEXT, action TEXT, detail TEXT);`);
  }
  close(){this.db.close();}
  row(table,id){const r=this.db.prepare(`SELECT data FROM ${table} WHERE id=?`).get(id); return r?JSON.parse(r.data):null;}
  confirmation(cardId, confirmationId) {
    const row = this.db.prepare('SELECT card,data FROM confirmations WHERE id=?').get(confirmationId);
    if (!row || row.card !== cardId) return null;
    return JSON.parse(row.data);
  }
  save(table,id,data){this.db.prepare(`UPDATE ${table} SET data=? WHERE id=?`).run(JSON.stringify(data),id);}
  log(actor,card,action,detail){this.db.prepare('INSERT INTO audit(at,actor,card,action,detail) VALUES(?,?,?,?,?)').run(this.now(),actor,card,action,JSON.stringify(detail));}
  transaction(fn) {this.db.exec('BEGIN IMMEDIATE');try{const result=fn();this.db.exec('COMMIT');return result;}catch(e){this.db.exec('ROLLBACK');throw e;}}
  register({id=randomUUID(),actor,corpId,botId,conversationId,cycle,start,end,ttlMs=3600000}) {
    checkDate(start);checkDate(end);
    if(!id||!actor||!corpId||!botId||!conversationId||cycle!==start.slice(0,7)||end<=start||!Number.isFinite(ttlMs)||ttlMs<=0)deny('INVALID_REGISTRATION');
    const card={id,mode:'sandbox',actor,corpId,botId,conversationId,cycle,revision:1,expires:this.now()+ttlMs,
      meeting:{kind:'fallback',cycle,start,end,status:'confirmed'},document:{open:false,closed:false,datetime:''}};
    this.db.prepare('INSERT INTO cards(id,data) VALUES(?,?)').run(card.id,JSON.stringify(card));
    return card;
  }
  authorize(card,principal){
    if(!card)deny('CARD_NOT_FOUND');
    // Principal must be supplied by a future validated transport, NEVER action context.
    if(!principal || principal.authenticated!==true || !['offline-test-harness','connector-stream'].includes(principal.source))deny('UNVERIFIED_TRANSPORT');
    for(const key of ['actor','corpId','botId','conversationId'])if(principal[key]!==card[key])deny('FORBIDDEN');
  }
  propose(cardId, form, principal, revision) {
    return this.transaction(()=>{
      const card=this.row('cards',cardId);this.authorize(card,principal);
      if(this.now()>=card.expires)deny('EXPIRED');
      if(card.revision!==revision)deny('STALE_CARD');
      if(card.pending)deny('RECONCILIATION_REQUIRED');
      const change=normalizeForm(form,this.now(),card.meeting);
      const id=randomUUID(), confirmation={id,actor:principal.actor,revision,change,changeDigest:digest(change),expires:Math.min(card.expires,this.now()+300000)};
      this.db.prepare('INSERT INTO confirmations(id,card,data) VALUES(?,?,?)').run(id,cardId,JSON.stringify(confirmation));
      this.log(principal.actor,cardId,'propose',{change});
      return {confirmationId:id,expires:confirmation.expires,change,summary:change.decision==='closed'?'【测试】不开兜底会；仅取消沙盒日程。':`【测试】召开兜底会：${change.start} 至 ${change.end}；仅修改沙盒日程。`};
    });
  }
  confirm(cardId,confirmationId,principal,{confirmed=false,simulateFailure=null}={}){
    if(!confirmed)deny('EXPLICIT_CONFIRMATION_REQUIRED');
    return this.transaction(()=>{
      const card=this.row('cards',cardId);this.authorize(card,principal);
      const confirmationRow=this.db.prepare('SELECT card,data FROM confirmations WHERE id=?').get(confirmationId);
      if(!confirmationRow||confirmationRow.card!==cardId)deny('CONFIRMATION_NOT_FOUND');
      const c=JSON.parse(confirmationRow.data);
      if(c.actor!==principal.actor)deny('FORBIDDEN');
      const old=this.row('receipts',confirmationId);if(old)return old;
      if(this.now()>=c.expires)deny('CONFIRMATION_EXPIRED');
      if(card.revision!==c.revision)deny('STALE_CARD');
      if(card.pending)deny('RECONCILIATION_REQUIRED');
      if(!timingSafeEqual(Buffer.from(c.changeDigest),Buffer.from(digest(c.change))))deny('CORRUPT_CONFIRMATION');
      const change=normalizeForm({...c.change,decision:[c.change.decision]},this.now(),card.meeting);
      let result;
      if(simulateFailure==='document') {
        result={mode:'sandbox',state:'failed',document:'unchanged',calendar:'unchanged',message:'【测试】裁决保存失败，日程未修改。'};
      }else{
        card.document={open:change.decision==='open',closed:change.decision==='closed',datetime:change.decision==='open'?change.start:''};
        if(simulateFailure==='calendar'||simulateFailure==='unknown'){
          card.pending=confirmationId;
          result={mode:'sandbox',state:simulateFailure==='unknown'?'unknown':'partial',document:'verified',calendar:simulateFailure==='unknown'?'unknown':'failed',message:'【测试】裁决已保存，日程未核验成功；需要对账，不重放。'};
        }else{
          card.meeting=change.decision==='closed'?{...card.meeting,status:'cancelled'}:{...card.meeting,start:change.start,end:change.end};
          result={mode:'sandbox',state:'completed',document:'verified',calendar:'verified',cardUpdate:simulateFailure==='card'?'failed':'pending',message:'【测试】沙盒裁决与沙盒日程已完成；未修改真实业务。'};
        }
        card.revision++;
      }
      this.save('cards',cardId,card);
      this.db.prepare('INSERT INTO receipts(id,card,data) VALUES(?,?,?)').run(confirmationId,cardId,JSON.stringify(result));
      this.log(principal.actor,cardId,'confirm',{confirmationId,change,result});
      return result;
    });
  }
}
