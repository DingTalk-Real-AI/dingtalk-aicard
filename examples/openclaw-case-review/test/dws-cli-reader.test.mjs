import test from 'node:test';
import assert from 'node:assert/strict';
import { createDwsCliReaders } from '../integration/dws-cli-reader.mjs';

function fakeExecutor(outputs) {
  const calls=[];
  const executor=(exe,args,options,callback)=>{
    calls.push({exe,args,options});
    const next=outputs.shift();
    callback(next?.error??null,next?.stdout??'',next?.stderr??'');
  };
  return {executor,calls};
}

test('DWS reader runs only fixed read commands without a shell and preserves profile/range', async()=>{
  const fake=fakeExecutor([{stdout:'{"success":true}'},{stdout:'{"success":true,"result":{"events":[],"hasMore":false}}'}]);
  const reader=createDwsCliReaders({dwsCliEntry:'/opt/dws/bin/dws.js',profile:'corp:user',execFileImpl:fake.executor,executable:'/usr/bin/node'});
  assert.deepEqual(await reader.readDocument({documentId:'doc-1'}),{success:true});
  assert.deepEqual(await reader.listEvents({start:'2026-11-01T00:00:00+08:00',end:'2026-12-01T00:00:00+08:00'}),{success:true,result:{events:[],hasMore:false}});
  assert.equal(fake.calls.length,2);
  assert.equal(fake.calls[0].options.shell,false);
  assert.deepEqual(fake.calls[0].args,['/opt/dws/bin/dws.js','doc','+fetch','--node','doc-1','--profile','corp:user','--format','json']);
  assert.ok(fake.calls[1].args.includes('calendar'));
});

test('DWS reader does not expose stderr and rejects invalid JSON, ranges, or executable paths', async()=>{
  const fake=fakeExecutor([{error:new Error('secret output'),stderr:'sensitive diagnostic'}]);
  const reader=createDwsCliReaders({dwsCliEntry:'/opt/dws/bin/dws.js',profile:'corp:user',execFileImpl:fake.executor,executable:'/usr/bin/node'});
  await assert.rejects(reader.readDocument({documentId:'doc-1'}),error=>error.message==='DWS_READ_COMMAND_FAILED'&&!error.message.includes('secret'));
  await assert.rejects(reader.listEvents({start:'bad',end:'also bad'}),/DWS_EVENT_RANGE_INVALID/);
  assert.throws(()=>createDwsCliReaders({dwsCliEntry:'relative.js',profile:'corp:user'}),/DWS_CLI_ENTRY_AND_PROFILE_REQUIRED/);
});

test('DWS reader follows returned cursors and returns one complete event set', async()=>{
  const fake=fakeExecutor([
    {stdout:JSON.stringify({success:true,result:{events:[{id:'event-1'}],hasMore:true,nextCursor:'cursor-2'}})},
    {stdout:JSON.stringify({success:true,result:{events:[{id:'event-2'}],hasMore:false}})},
  ]);
  const reader=createDwsCliReaders({dwsCliEntry:'/opt/dws/bin/dws.js',profile:'corp:user',execFileImpl:fake.executor,executable:'/usr/bin/node'});
  const result=await reader.listEvents({start:'2026-11-01T00:00:00+08:00',end:'2026-12-01T00:00:00+08:00'});
  assert.deepEqual(result.result.events.map(event=>event.id),['event-1','event-2']);
  assert.equal(result.result.hasMore,false);
  assert.deepEqual(fake.calls[1].args.slice(-4),['--cursor','cursor-2','--format','json']);
});
