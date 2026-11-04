import test from 'node:test';
import assert from 'node:assert/strict';
import { createDwsCliReaders } from '../integration/dws-cli-reader.mjs';

function fakeExecutor(outputs) {
  const calls=[];
  const executor=async(argv,options)=>{
    calls.push({argv,options});
    const next=outputs.shift();
    if(next?.error) throw next.error;
    return {
      code:next?.code??0,
      stdout:next?.stdout??'',
      stderr:next?.stderr??'',
      stdoutTruncatedBytes:next?.stdoutTruncatedBytes??0,
      killed:next?.killed??false,
      outputLimitExceeded:next?.outputLimitExceeded??false,
    };
  };
  return {executor,calls};
}

test('DWS reader runs only fixed argv commands with bounded output and process-tree cleanup', async()=>{
  const fake=fakeExecutor([{stdout:'{"success":true}'},{stdout:'{"success":true,"result":{"events":[],"hasMore":false}}'}]);
  const reader=createDwsCliReaders({dwsCliEntry:'/opt/dws/bin/dws.js',profile:'corp:user',commandImpl:fake.executor,executable:'/usr/bin/node'});
  assert.deepEqual(await reader.readDocument({documentId:'doc-1'}),{success:true});
  assert.deepEqual(await reader.listEvents({start:'2026-11-01T00:00:00+08:00',end:'2026-12-01T00:00:00+08:00'}),{success:true,result:{events:[],hasMore:false}});
  assert.equal(fake.calls.length,2);
  assert.deepEqual(fake.calls[0].argv,['/usr/bin/node','/opt/dws/bin/dws.js','doc','+fetch','--node','doc-1','--profile','corp:user','--format','json']);
  assert.equal(fake.calls[0].options.timeoutMs,15000);
  assert.equal(fake.calls[0].options.killProcessTree,true);
  assert.equal(fake.calls[0].options.requireProcessTreeExtinction,true);
  assert.deepEqual(fake.calls[0].options.maxOutputBytes,{stdout:1_048_576,stderr:16_384});
  assert.equal(fake.calls[0].options.outputCapture,'head');
  assert.equal(fake.calls[0].options.terminateOnOutputLimit,true);
  assert.ok(fake.calls[1].argv.includes('calendar'));
});

test('DWS reader does not expose stderr and rejects invalid JSON, ranges, or executable paths', async()=>{
  const fake=fakeExecutor([{error:new Error('secret output'),stderr:'sensitive diagnostic'}]);
  const reader=createDwsCliReaders({dwsCliEntry:'/opt/dws/bin/dws.js',profile:'corp:user',commandImpl:fake.executor,executable:'/usr/bin/node'});
  await assert.rejects(reader.readDocument({documentId:'doc-1'}),error=>error.message==='DWS_READ_COMMAND_FAILED'&&!error.message.includes('secret'));
  await assert.rejects(reader.listEvents({start:'bad',end:'also bad'}),/DWS_EVENT_RANGE_INVALID/);
  assert.throws(()=>createDwsCliReaders({dwsCliEntry:'relative.js',profile:'corp:user'}),/DWS_CLI_ENTRY_AND_PROFILE_REQUIRED/);
});

test('DWS reader follows returned cursors and returns one complete event set', async()=>{
  const fake=fakeExecutor([
    {stdout:JSON.stringify({success:true,result:{events:[{id:'event-1'}],hasMore:true,nextCursor:'cursor-2'}})},
    {stdout:JSON.stringify({success:true,result:{events:[{id:'event-2'}],hasMore:false}})},
  ]);
  const reader=createDwsCliReaders({dwsCliEntry:'/opt/dws/bin/dws.js',profile:'corp:user',commandImpl:fake.executor,executable:'/usr/bin/node'});
  const result=await reader.listEvents({start:'2026-11-01T00:00:00+08:00',end:'2026-12-01T00:00:00+08:00'});
  assert.deepEqual(result.result.events.map(event=>event.id),['event-1','event-2']);
  assert.equal(result.result.hasMore,false);
  assert.deepEqual(fake.calls[1].argv.slice(-4),['--cursor','cursor-2','--format','json']);
});
