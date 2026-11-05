import { DatabaseSync } from 'node:sqlite';

// Dedicated sandbox DB only. No production config, credential or message-store access.
export class CardStore {
  constructor(path) {
    this.db = new DatabaseSync(path);
    this.db.exec(`PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA busy_timeout=5000;
      CREATE TABLE IF NOT EXISTS binding (id INTEGER PRIMARY KEY CHECK(id=1), accountId TEXT NOT NULL, senderId TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS cards (outTrackId TEXT PRIMARY KEY, requestKey TEXT UNIQUE NOT NULL,
        accountId TEXT NOT NULL, senderId TEXT NOT NULL, sessionKey TEXT NOT NULL,
        surfaceId TEXT NOT NULL, nonce TEXT NOT NULL, revision INTEGER NOT NULL,
        expiresAt INTEGER NOT NULL, state TEXT NOT NULL, updateId TEXT NOT NULL,
        lastOutcome TEXT, receipt TEXT);
      CREATE TABLE IF NOT EXISTS card_instance (cardInstanceId TEXT PRIMARY KEY, outTrackId TEXT UNIQUE NOT NULL);
      CREATE TABLE IF NOT EXISTS callback_receipt (messageId TEXT PRIMARY KEY, outTrackId TEXT NOT NULL, outcome TEXT NOT NULL);
      UPDATE cards SET state='unknown', lastOutcome='unknown' WHERE state IN ('creating','updating');`);
  }
  transaction(fn) {
    this.db.exec('BEGIN IMMEDIATE');
    try { const value = fn(); this.db.exec('COMMIT'); return value; }
    catch (e) { this.db.exec('ROLLBACK'); throw e; }
  }
  get(id) { return this.db.prepare('SELECT * FROM cards WHERE outTrackId=?').get(id); }
  byRequest(key) { return this.db.prepare('SELECT * FROM cards WHERE requestKey=?').get(key); }
  bindInstance(cardInstanceId, outTrackId) {
    return this.transaction(() => {
      const row = this.get(outTrackId);
      if (!row || row.state !== 'accepted' || !cardInstanceId) return false;
      this.db.prepare('INSERT INTO card_instance VALUES (?,?)').run(cardInstanceId, outTrackId);
      return true;
    });
  }
  byInstance(cardInstanceId) {
    return this.db.prepare('SELECT cards.* FROM card_instance JOIN cards USING(outTrackId) WHERE cardInstanceId=?').get(cardInstanceId);
  }
  claimCallback(messageId,outTrackId) {
    return this.transaction(()=>{
      const previous=this.db.prepare('SELECT outTrackId,outcome FROM callback_receipt WHERE messageId=?').get(messageId);
      if(previous)return {status:'duplicate',outcome:previous.outcome};
      this.db.prepare('INSERT INTO callback_receipt(messageId,outTrackId,outcome) VALUES(?,?,?)').run(messageId,outTrackId,'processing');
      return {status:'claimed'};
    });
  }
  finishCallback(messageId,outcome) {
    return this.db.prepare("UPDATE callback_receipt SET outcome=? WHERE messageId=? AND outcome='processing'").run(outcome,messageId).changes===1;
  }
  reserve(row, maxRows) {
    return this.transaction(() => {
      const bound = this.db.prepare('SELECT * FROM binding WHERE id=1').get();
      if (bound && (bound.accountId !== row.accountId || bound.senderId !== row.senderId)) return { status: 'target_mismatch' };
      const previous = this.byRequest(row.requestKey);
      if (previous) return { status: 'existing', row: previous };
      if (this.db.prepare("SELECT 1 FROM cards WHERE state IN ('unknown','creating','updating') LIMIT 1").get()) return { status: 'unresolved_previous_request' };
      if (this.db.prepare('SELECT count(*) AS n FROM cards').get().n >= maxRows) return { status: 'capacity_reached' };
      this.db.prepare('INSERT OR IGNORE INTO binding VALUES (1,?,?)').run(row.accountId, row.senderId);
      this.db.prepare(`UPDATE cards SET state='stale' WHERE accountId=? AND senderId=? AND state='accepted'`).run(row.accountId, row.senderId);
      this.db.prepare(`INSERT INTO cards (outTrackId,requestKey,accountId,senderId,sessionKey,surfaceId,nonce,revision,expiresAt,state,updateId)
        VALUES (?,?,?,?,?,?,?,?,?,'creating',?)`).run(row.outTrackId,row.requestKey,row.accountId,row.senderId,row.sessionKey,row.surfaceId,row.nonce,row.revision,row.expiresAt,row.updateId);
      return { status: 'reserved', row: this.get(row.outTrackId) };
    });
  }
  transition(id, from, state) {
    return this.db.prepare('UPDATE cards SET state=? WHERE outTrackId=? AND state=?').run(state,id,from).changes === 1;
  }
  outcome(id, from, state, result) {
    // Store only bounded non-secret status and minimal receipt, never HTTP headers/body/tokens.
    return this.db.prepare('UPDATE cards SET state=?,lastOutcome=?,receipt=? WHERE outTrackId=? AND state=?')
      .run(state,result.status,JSON.stringify(result.receipt ?? {}),id,from).changes === 1;
  }
  close() { this.db.close(); }
}
