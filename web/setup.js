'use strict';
(() => {
  const steps=['environment','wechat','api','model'];
  const firstIncomplete=checks=>{const index=steps.findIndex(name=>!checks?.[name]);return index<0?steps.length:index;};
  const canAdvance=(index,checks)=>index>=0&&index<steps.length&&steps.slice(0,index+1).every(name=>checks?.[name]===true);
  if(typeof module!=='undefined'&&module.exports)module.exports={steps,firstIncomplete,canAdvance};
  if(typeof document==='undefined')return;
  const modal=document.createElement('dialog');modal.id='setup-dialog';modal.className='setup-dialog';
  modal.setAttribute('aria-labelledby','setup-title');
  modal.innerHTML=[
    '<form method="dialog" class="setup-heading"><h1 id="setup-title">设置 ChatSeek</h1><button aria-label="关闭设置">×</button></form>',
    '<div class="setup-progress"><span id="setup-step-count"></span><span id="setup-step-title"></span></div><div class="setup-progress-track"><div id="setup-progress-bar"></div></div>',
    '<section data-setup-step="0"><h2>检查运行环境</h2><p>先确认这台电脑可以运行微信读取流程。</p><ul id="setup-environment-checks" class="setup-checklist"></ul>',
    '<p id="setup-environment"></p><button id="setup-check-environment" type="button">检查运行环境</button><p id="setup-status-0" class="setup-status" role="status"></p>',
    '<details class="setup-details"><summary>数据与运行方式</summary><p>聊天在本机保存和检索。启用云端 AI 时，会把问题、相关聊天及必要问答历史发送至你配置的 API；完整范围总结可能发送该范围全部文字。</p><p id="setup-directory" class="setup-muted"></p><p id="setup-exit-guide" class="setup-muted"></p></details></section>',
    '<section data-setup-step="1" hidden><h2>接入本机微信</h2><p>选择本机账号，再初始化并检查读取权限。已有密钥时直接检查，无需重新获取。</p>',
    '<label>本机账号<select id="setup-account"></select></label><button id="setup-save-account" type="button">选择账号</button>',
    '<details id="setup-bootstrap-guide" class="setup-details"><summary>首次初始化说明</summary><p>先在原版微信登录一次，再正常退出微信。初始化会复制微信、重签临时副本并通过 LLDB 启动，验证密钥后保存到本机。原版微信签名保持不变；如临时窗口要求登录，请按提示操作。</p></details>',
    '<label id="setup-ack-label"><input id="setup-ack" type="checkbox">我已正常退出微信，理解并同意临时副本和调试初始化流程。</label>',
    '<div class="setup-inline-actions"><button id="setup-bootstrap" type="button">初始化微信读取</button><button id="setup-check-wechat" type="button">检查数据库读取</button></div>',
    '<p id="setup-status-1" class="setup-status" role="status"></p><button id="setup-permission" type="button" class="setup-secondary">打开系统权限设置</button></section>',
    '<section data-setup-step="2" hidden><h2>连接你的大模型</h2><p>填写服务商提供的通用 API 配置，支持 Chat Completions 兼容接口。费用由你与服务商结算。</p>',
    '<label>Base URL<input id="setup-base-url" type="url" placeholder="https://api.example.com/v1" autocomplete="off" spellcheck="false"></label>',
    '<label>Model Name<input id="setup-text-model" placeholder="服务商提供的模型名称" autocomplete="off" spellcheck="false"></label>',
    '<label>API Key<input id="setup-key" type="password" autocomplete="off" placeholder="填写你的 API Key"></label>',
    '<details class="setup-details"><summary>视觉模型与兼容选项</summary><label>Vision Model Name（可选）<input id="setup-vision-model" autocomplete="off" placeholder="留空则仅处理文字"></label>',
    '<label>上下文容量（tokens）<input id="setup-context-tokens" type="number" min="1024" max="1000000" value="128000"></label><label>回答输出上限（tokens）<input id="setup-output-tokens" type="number" min="128" max="32768" value="8192"></label>',
    '<label><input id="setup-json-mode" type="checkbox" checked>请求 JSON 输出格式（服务商不支持时可取消，模型仍须返回有效 JSON）</label></details>',
    '<p class="setup-muted">验证仅发送一条简短测试请求，不含聊天，服务商可能收取少量费用。API Key 保存在本机，不会在页面回显。</p>',
    '<button id="setup-save-key" type="button">验证并保存</button><p id="setup-status-2" class="setup-status" role="status"></p></section>',
    '<section data-setup-step="3" hidden><h2>准备本地检索</h2><p id="setup-model-description"></p><div class="setup-model-card"><span class="setup-model-icon">↗</span><div><strong>本地向量模型</strong><small>BAAI/bge-small-zh-v1.5 · 本机 CPU 计算</small></div></div>',
    '<button id="setup-model" type="button">下载并检查模型</button><p id="setup-status-3" class="setup-status" role="status"></p></section>',
    '<section id="setup-complete" class="setup-complete" hidden><div class="setup-complete-icon">✓</div><h2>配置检查已完成</h2><p>现在可以在左侧输入好友备注、昵称或群名，选择会话并同步聊天。</p><button id="setup-done" type="button" class="setup-primary">选择会话</button></section>',
    '<div id="setup-navigation" class="setup-navigation"><button id="setup-back" type="button" class="setup-secondary">上一步</button><button id="setup-next" type="button" class="setup-primary" disabled>下一步</button></div>'
  ].join('');
  document.body.append(modal);
  const entry=document.createElement('button');entry.type='button';entry.className='text-button';entry.textContent='模型与接入配置';
  document.querySelector('.sidebar-bottom').before(entry);
  const el=id=>document.getElementById(id);
  let info=null,current=0,busy=false,timer,fieldsLoaded=false,apiDirty=false,autoModelAttempted=false;
  const messages=['','','',''],errors=[false,false,false,false];
  const titles=['运行环境','微信接入','模型 API','本地检索'];
  const ready=()=>info?.checks||{};
  function feedback(step,message,error=false){messages[step]=message;errors[step]=error;render();}
  function render(){
    if(!info)return;
    modal.querySelectorAll('[data-setup-step]').forEach((section,index)=>section.hidden=index!==current);
    el('setup-complete').hidden=current!==4;el('setup-navigation').hidden=current===4;
    el('setup-step-count').textContent=current===4?'全部完成':'步骤 '+(current+1)+' / 4';
    el('setup-step-title').textContent=titles[current]||'开始使用';
    el('setup-progress-bar').style.width=(current===4?100:(current+1)*25)+'%';
    el('setup-back').hidden=current===0;el('setup-back').disabled=busy;
    el('setup-next').disabled=busy||!canAdvance(current,ready())||(current===2&&apiDirty);
    el('setup-next').textContent=current===3?'完成设置':'下一步';
    const defaults=[
      ready().environment?'运行环境检查通过，可以继续。':'检查通过后，才能继续接入微信。',
      ready().wechat?'数据库只读访问检查通过。':info.bootstrap.running?info.bootstrap.message:info.keys_ready?'已找到本机密钥，请检查数据库读取。':info.bootstrap.message||'请选择账号并完成初始化。',
      apiDirty?'配置已修改，请重新验证。':ready().api?'API 连接与 JSON 回复检查通过。':info.api_configured?'已保存模型配置，请验证后继续。':'填写三项配置，再验证连接。',
      info.local_model.error||(ready().model?'本地向量模型已就绪。':info.local_model.running?info.local_model.message:info.local_model.available?'已检测到本地模型，正在准备检查。':'模型仅首次下载，后续会直接复用本机文件。')
    ];
    for(let index=0;index<4;index++){
      const status=el('setup-status-'+index);
      status.textContent=messages[index]||defaults[index];
      status.dataset.tone=errors[index]||(index===3&&info.local_model.error)?'error':ready()[steps[index]]?'success':'pending';
    }
    el('setup-environment').textContent='微信 '+(info.wechat_version||'未检测到')+' · 已验证版本 '+info.tested_wechat_version+'（其他版本需实测）';
    el('setup-directory').textContent='本机数据目录：'+info.data_dir;
    el('setup-exit-guide').textContent=info.runtime==='app'?'关闭网页不会退出应用。请用 Dock 的“退出 ChatSeek”停止后台。':'关闭网页不会停止源码后台；从终端启动时，请在启动终端按 Ctrl+C 退出。';
    const checks=el('setup-environment-checks');checks.replaceChildren();
    for(const item of info.environment.checks){const li=document.createElement('li');li.textContent=(item.passed?'✓ ':'○ ')+item.label;li.dataset.passed=String(item.passed);checks.append(li);}
    const account=el('setup-account'),previous=account.value;
    account.replaceChildren();
    for(const item of info.accounts){const option=document.createElement('option');option.value=item.id;option.textContent=item.label;account.append(option);}
    if(previous&&info.accounts.some(item=>item.id===previous))account.value=previous;
    account.disabled=busy||info.keys_ready||info.contact_ready;
    el('setup-save-account').disabled=busy||info.keys_ready||info.contact_ready||!info.accounts.length;
    el('setup-ack-label').hidden=info.keys_ready;el('setup-bootstrap-guide').hidden=info.keys_ready;
    el('setup-bootstrap').hidden=info.keys_ready;
    el('setup-bootstrap').disabled=busy||info.bootstrap.running||!info.account_ready||!el('setup-ack').checked;
    el('setup-check-wechat').disabled=busy||!info.keys_ready||info.bootstrap.running;
    el('setup-check-environment').disabled=busy;el('setup-save-key').disabled=busy;
    modal.querySelectorAll('[data-setup-step="2"] input').forEach(input=>input.disabled=busy);
    el('setup-model-description').textContent=info.local_model.source==='bundled'?'下载包已附带向量模型，直接在本机检查和加载，无需再次下载。':info.local_model.available?'已找到本机模型文件，只做加载检查，无需重复下载。':'当前源码未附带模型。首次需下载约 100 MB 的公共权重；也可以改用 GitHub Releases 中的“含模型源码包”。聊天不会发送给外部向量服务。';
    el('setup-model').hidden=ready().model;
    el('setup-model').disabled=busy||info.local_model.running;
    el('setup-model').textContent=info.local_model.running?'正在准备…':info.local_model.available?'检查本地模型':'下载并检查模型';
    el('setup-key').placeholder=info.api_configured?'已保存；留空沿用原 Key，填写则替换':'填写你的 API Key';
    if(current===3&&info.local_model.available&&!ready().model&&!info.local_model.running&&!autoModelAttempted&&!busy){
      autoModelAttempted=true;queueMicrotask(()=>prepareModel());
    }
  }
  async function load(){
    clearTimeout(timer);info=await api('setup');
    if(!fieldsLoaded){
      const config=info.model_config;
      // An empty installation should not look tied to a particular provider.
      el('setup-base-url').value=info.api_configured?config.base_url:'';
      el('setup-text-model').value=info.api_configured?config.model:'';
      el('setup-vision-model').value=info.api_configured?config.vision_model:'';
      el('setup-context-tokens').value=config.context_tokens;el('setup-output-tokens').value=config.max_output_tokens;
      el('setup-json-mode').checked=config.json_mode;fieldsLoaded=true;
    }
    render();
    if(modal.open&&(info.bootstrap.running||info.local_model.running))timer=setTimeout(()=>load().catch(error=>feedback(current,error.message,true)),1500);
  }
  async function run(step,task){
    if(busy)return;busy=true;messages[step]='正在检查，请稍候…';errors[step]=false;render();
    try{await task();messages[step]='';await load();}
    catch(error){messages[step]=error.message;errors[step]=true;}
    finally{busy=false;render();}
  }
  async function prepareModel(){await run(3,()=>api('setup-model',{}));}
  window.showChatSeekSetup=async()=>{
    if(!modal.open)modal.showModal();
    try{await load();current=firstIncomplete(ready());render();modal.scrollTop=0;}
    catch(error){el('setup-status-0').textContent=error.message;}
  };
  entry.addEventListener('click',window.showChatSeekSetup);
  modal.addEventListener('close',()=>clearTimeout(timer));
  el('setup-ack').onchange=render;
  el('setup-check-environment').onclick=()=>run(0,()=>api('setup-check-environment',{}));
  el('setup-save-account').onclick=()=>run(1,()=>api('setup-account',{account:el('setup-account').value}));
  el('setup-bootstrap').onclick=()=>run(1,()=>api('setup-bootstrap',{acknowledged:el('setup-ack').checked}));
  el('setup-check-wechat').onclick=()=>run(1,()=>api('setup-check-wechat',{}));
  el('setup-permission').onclick=()=>run(1,()=>api('access-settings',{}));
  modal.querySelectorAll('[data-setup-step="2"] input').forEach(input=>input.addEventListener('input',()=>{apiDirty=true;messages[2]='';errors[2]=false;render();}));
  el('setup-save-key').onclick=()=>run(2,async()=>{
    await api('setup-verify-api',{key:el('setup-key').value,base_url:el('setup-base-url').value,model:el('setup-text-model').value,
      vision_model:el('setup-vision-model').value,context_tokens:Number(el('setup-context-tokens').value),
      max_output_tokens:Number(el('setup-output-tokens').value),json_mode:el('setup-json-mode').checked});
    el('setup-key').value='';apiDirty=false;
  });
  el('setup-model').onclick=()=>{autoModelAttempted=true;prepareModel();};
  el('setup-back').onclick=()=>{if(!busy&&current>0){current--;render();modal.scrollTop=0;}};
  el('setup-next').onclick=()=>{if(!el('setup-next').disabled&&canAdvance(current,ready())){current++;render();modal.scrollTop=0;}};
  el('setup-done').onclick=()=>{modal.close();refresh().catch(error=>notice(error.message));};
  if(window.chatseekSetupShown)window.showChatSeekSetup();
})();
