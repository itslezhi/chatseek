'use strict';

async function localRequest(url,options){
  try{return await fetch(url,options);}
  catch{throw new Error('无法连接本机 ChatSeek 服务，请重新启动网页服务后重试。输入的问题会保留。');}
}

// POST streaming transport: UTF-8 characters and JSON frames can span reads.
async function readChatStream(response, onEvent) {
  if(!response.body || !response.headers.get('Content-Type')?.includes('application/x-ndjson')){
    throw new Error('未收到流式回答，请刷新页面后重试。');
  }
  const reader=response.body.getReader(), decoder=new TextDecoder('utf-8',{fatal:true});
  let pending='';
  try{
    while(true){
      let packet;
      try{packet=await reader.read();}
      catch{throw new Error('与本机 ChatSeek 服务的连接已中断，请检查服务是否仍在运行后重试。');}
      const {value,done}=packet;
      pending+=decoder.decode(value,{stream:!done});
      let boundary;
      while((boundary=pending.indexOf('\n'))!==-1){
        const line=pending.slice(0,boundary);pending=pending.slice(boundary+1);
        if(!line.trim())continue;
        let event;
        try{event=JSON.parse(line);}catch{throw new Error('回答传输异常，请重试。');}
        if(event.type==='error')throw new Error((event.error || '回答未完成，请重试。').replaceAll('微信','聊天客户端'));
        if(event.type==='done'){
          if(typeof event.result?.answer!=='string')throw new Error('回答完成信息异常，请刷新后重试。');
          return event.result;
        }
        if(event.type==='delta' && typeof event.text!=='string')throw new Error('回答传输异常，请重试。');
        onEvent(event);
      }
      if(done)throw new Error('连接中断，未收到完整回答，请重试。');
    }
  }finally{
    // Closing a page or rejecting a frame should also close the server request.
    try{await reader.cancel();}catch{}
    reader.releaseLock();
  }
}
