'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const context=vm.createContext({TextDecoder,Error});
vm.runInContext(fs.readFileSync('web/stream.js','utf8'),context);
const read=context.readChatStream;
const encoder=new TextEncoder();
const frame=event=>JSON.stringify(event)+'\n';
const response=bytes=>new Response(new ReadableStream({start(controller){for(const byte of bytes)controller.enqueue(Uint8Array.of(byte));controller.close();}}),{headers:{'Content-Type':'application/x-ndjson; charset=utf-8'}});

test('split UTF-8 and JSON frames preserve Chinese deltas and final result',async()=>{
  const events=[];
  const body=frame({type:'status',message:'准备中'})+frame({type:'delta',text:'今天'})+frame({type:'delta',text:'完成了报告。'})+frame({type:'done',result:{answer:'今天完成了报告。'}});
  const result=await read(response(encoder.encode(body)),event=>events.push(event));
  assert.equal(events[1].text+events[2].text,result.answer);
});
test('delta callback runs while completion is still pending',async()=>{
  let controller;
  const res=new Response(new ReadableStream({start(c){controller=c;}}),{headers:{'Content-Type':'application/x-ndjson'}});
  let first;const gotFirst=new Promise(resolve=>first=resolve);
  let finished=false;
  const completion=read(res,event=>{if(event.type==='delta')first(event.text);}).then(result=>{finished=true;return result;});
  controller.enqueue(encoder.encode(frame({type:'delta',text:'部分'})));
  assert.equal(await gotFirst,'部分');assert.equal(finished,false);
  controller.enqueue(encoder.encode(frame({type:'done',result:{answer:'部分及其余内容'}})));
  assert.equal((await completion).answer,'部分及其余内容');
});
test('EOF or malformed stream cannot turn partial content into success',async()=>{
  for(const body of [frame({type:'delta',text:'部分'}),'bad json\n',frame({type:'done',result:{}})]){
    await assert.rejects(read(response(encoder.encode(body)),()=>{}));
  }
});
test('server error after a delta is propagated instead of accepted as complete',async()=>{
  const body=frame({type:'delta',text:'部分'})+frame({type:'error',error:'模型连接中断'});
  await assert.rejects(read(response(encoder.encode(body)),()=>{}),/模型连接中断/);
});

test('connection failure has a local-service explanation instead of Failed to fetch',async()=>{
  const offline=vm.createContext({TextDecoder,Error,fetch:async()=>{throw new TypeError('Failed to fetch');}});
  vm.runInContext(fs.readFileSync('web/stream.js','utf8'),offline);
  await assert.rejects(offline.localRequest('/api/ask',{}),/无法连接本机 ChatSeek 服务/);
});
test('native reader network failure has an actionable connection message',async()=>{
  const res=new Response(new ReadableStream({start(controller){controller.error(new TypeError('Failed to fetch'));}}),{headers:{'Content-Type':'application/x-ndjson'}});
  await assert.rejects(read(res,()=>{}),/连接已中断/);
});
