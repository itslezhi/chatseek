'use strict';
(() => {
  const steps=['environment','wechat','api','model'];
  const firstIncomplete=checks=>{const index=steps.findIndex(name=>!checks?.[name]);return index<0?steps.length:index;};
  const canAdvance=(index,checks)=>index>=0&&index<steps.length&&steps.slice(0,index+1).every(name=>checks?.[name]===true);
  function accountView(connection,editing=false){
    const state=editing&&connection.can_change_account?'choose_account':connection.state;
    const descriptions={
      no_data:['首次接入 · 未发现微信数据','ChatSeek 需要先找到这台电脑保存的微信数据。'],
      choose_account:['首次接入 · 确认微信数据','请先在原版微信确认你要使用的账号，再在这里确认对应的本机数据。确认前不会自动接入。'],
      initialize:['已选择数据 · 等待首次接入','已确认需要使用的微信数据。接下来先正常退出原版微信，再开始首次接入。'],
      initializing:['正在首次接入','请留意临时微信窗口，并按提示完成登录。接入完成后再验证读取。'],
      initialized:['首次接入完成 · 等待验证','已为刚才选择的微信数据生成本机读取凭据。接下来验证读取，并核对账号昵称。'],
      saved_connection:['以前接入过 · 等待验证','ChatSeek 在这个数据目录中保存过账号和读取凭据。无需重新选择或首次接入，验证后即可继续。'],
      verified:['账号读取已验证','这份微信数据已经可以读取，可以继续下一步。'],
      needs_attention:['接入配置需要检查',connection.problem]
    };
    return {badge:descriptions[state]?.[0]||'接入微信',intro:descriptions[state]?.[1]||'',
      chooser:state==='choose_account'&&connection.detected_count>0,
      dropdown:state==='choose_account'&&connection.detected_count>1,
      noData:state==='no_data',selected:!!connection.selected&&state!=='choose_account',
      initialize:state==='initialize',verify:['initialized','saved_connection','verified'].includes(state),
      change:state==='initialize'&&connection.can_change_account,
      refresh:['no_data','choose_account'].includes(state)};
  }
  if(typeof module!=='undefined'&&module.exports)module.exports={steps,firstIncomplete,canAdvance,accountView};
  if(typeof document==='undefined')return;
  const modal=document.createElement('dialog');modal.id='setup-dialog';modal.className='setup-dialog';
  modal.setAttribute('aria-labelledby','setup-title');
  modal.innerHTML=[
    '<form method="dialog" class="setup-heading"><h1 id="setup-title">设置 ChatSeek</h1><button aria-label="关闭设置">×</button></form>',
    '<div class="setup-progress"><span id="setup-step-count"></span><span id="setup-step-title"></span></div><div class="setup-progress-track"><div id="setup-progress-bar"></div></div>',
    '<section data-setup-step="0"><h2>检查运行环境</h2><p>先确认这台电脑可以运行微信读取流程。</p><ul id="setup-environment-checks" class="setup-checklist"></ul>',
    '<p id="setup-environment"></p><button id="setup-check-environment" type="button">检查运行环境</button><p id="setup-status-0" class="setup-status" role="status"></p>',
    '<details class="setup-details"><summary>数据与运行方式</summary><p>聊天在本机保存和检索。启用云端 AI 时，会把问题、相关聊天及必要问答历史发送至你配置的 API；完整范围总结可能发送该范围全部文字。</p><p id="setup-directory" class="setup-muted"></p><p id="setup-exit-guide" class="setup-muted"></p></details></section>',
    '<section data-setup-step="1" hidden><h2>接入你的微信</h2><p id="setup-account-intro"></p><span id="setup-account-badge" class="setup-account-badge"></span>',
    '<div id="setup-account-empty" class="setup-account-empty" hidden><strong>还没有发现本机微信数据</strong><p>先打开原版微信并登录你要使用的账号，然后点击“刷新账号列表”。安装包不包含微信读取凭据，首次接入会在这台电脑单独生成。</p></div>',
    '<div id="setup-account-chooser" hidden><p id="setup-account-discovery"></p><label id="setup-account-label">选择要使用的微信数据<select id="setup-account"></select></label><p id="setup-account-choice-help" class="setup-muted"></p><button id="setup-save-account" type="button" class="setup-primary">使用这份微信数据</button></div>',
    '<button id="setup-refresh-accounts" type="button" class="setup-secondary">刷新账号列表</button>',
    '<div id="setup-saved-account" class="setup-account-card" hidden><small id="setup-account-caption"></small><strong id="setup-account-name"></strong><p id="setup-account-note"></p><details class="setup-details"><summary>查看数据目录标识</summary><p id="setup-account-identifier" class="setup-muted"></p><p class="setup-muted">这个标识来自本机数据目录，不是微信昵称或手机号。</p></details><button id="setup-change-account" type="button" class="setup-secondary" hidden>重新选择数据</button></div>',
    '<div id="setup-first-connect" hidden>',
    '<details id="setup-bootstrap-guide" class="setup-details"><summary>首次初始化说明</summary><p>先在原版微信登录一次，再正常退出微信。初始化会复制微信、重签临时副本并通过 LLDB 启动，验证密钥后保存到本机。原版微信签名保持不变；如临时窗口要求登录，请按提示操作。</p></details>',
    '<label id="setup-ack-label"><input id="setup-ack" type="checkbox">我已正常退出微信，理解并同意临时副本和调试初始化流程。</label>',
    '<button id="setup-bootstrap" type="button" class="setup-primary">开始首次接入</button><p class="setup-muted">首次接入生成的读取凭据只保存在这台电脑。换电脑后通常需要重新接入。</p></div>',
    '<button id="setup-check-wechat" type="button" class="setup-primary" hidden>验证已有接入</button><p id="setup-status-1" class="setup-status" role="status"></p>',
    '<details class="setup-details"><summary>账号不对或无法读取？</summary><p id="setup-account-help"></p><p>账号列表来自这台电脑保存过的微信数据，不代表微信当前的登录状态。没有发现数据或读取失败时，请先确认原版微信已登录，并检查文件访问权限。</p><button id="setup-permission" type="button" class="setup-secondary">打开系统权限设置</button></details></section>',
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
  let info=null,current=0,busy=false,timer,fieldsLoaded=false,apiDirty=false,autoModelAttempted=false,choosingAccount=false;
  const messages=['','','',''],errors=[false,false,false,false];
  const titles=['运行环境','微信接入','模型 API','本地检索'];
  const ready=()=>info?.checks||{};
  function feedback(step,message,error=false){messages[step]=message;errors[step]=error;render();}
  function render(){
    if(!info)return;
    const connection=info.wechat_connection,accountUI=accountView(connection,choosingAccount);
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
      ready().wechat?(connection.selected?.nickname_verified?'读取检查通过，已从本机联系人信息核对账号昵称。':'读取检查通过；暂未获取到昵称，请在原版微信确认账号。'):info.bootstrap.running?info.bootstrap.message:connection.state==='initialized'?'首次接入已完成，请点击“验证读取并核对账号”。':connection.state==='saved_connection'?'点击“验证已有接入”确认仍可读取，不会重新获取凭据。':connection.state==='initialize'?'尚未进行首次接入，请按上面的说明继续。':connection.state==='needs_attention'?connection.problem:connection.state==='no_data'?'刷新只会检查本机数据，不会自动开始接入。':'尚未确认要使用的微信数据，请先确认。',
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
    el('setup-account-intro').textContent=accountUI.intro;
    el('setup-account-badge').textContent=accountUI.badge;
    el('setup-account-empty').hidden=!accountUI.noData;
    el('setup-account-chooser').hidden=!accountUI.chooser;
    el('setup-account-label').hidden=!accountUI.dropdown;
    el('setup-account-discovery').textContent='检测到 '+connection.detected_count+' 份本机微信数据。';
    el('setup-account-choice-help').textContent=connection.detected_count>1?'这些数据可能来自以前登录过的不同账号。接入前暂时无法读取昵称，目录标识仅用于区分数据；无法确认时请先不要选择。':'检测到数据不代表已经接入。确认原版微信中的账号后，点击“使用这份微信数据”。';
    const account=el('setup-account'),previous=account.value;
    account.replaceChildren();
    if(connection.choices.length>1){const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent='请选择微信数据';account.append(placeholder);}
    for(const item of connection.choices){const option=document.createElement('option');option.value=item.id;option.textContent=item.label;account.append(option);}
    if(previous&&connection.choices.some(item=>item.id===previous))account.value=previous;
    account.disabled=busy;
    el('setup-save-account').textContent=connection.detected_count>1?'使用选中的微信数据':'使用这份微信数据';
    el('setup-save-account').disabled=busy||!account.value;
    el('setup-refresh-accounts').hidden=!accountUI.refresh;el('setup-refresh-accounts').disabled=busy;
    el('setup-saved-account').hidden=!accountUI.selected;
    el('setup-account-caption').textContent=connection.selected?.nickname_verified?'已核对的微信昵称':ready().wechat?'读取已验证的微信数据':['initialized','initializing'].includes(connection.state)?'本次接入的账号':connection.credentials_saved?'上次接入的账号':'你选择的微信数据';
    el('setup-account-name').textContent=connection.selected?.label||'';
    el('setup-account-note').textContent=connection.selected?.nickname_verified?'昵称已从这份微信数据的本机联系人信息核对。':connection.state==='initializing'?'首次接入仍在进行，请按临时微信窗口的提示完成。':connection.state==='initialized'?'本次首次接入生成的读取凭据已保存到本机，验证后能够识别昵称时会在这里显示。':connection.credentials_saved?'验证读取后，能够识别昵称时会在这里显示。这是上次保存的接入配置，不代表微信当前已登录。':'数据已选择，但还没有完成首次接入。';
    el('setup-account-identifier').textContent=connection.selected?.identifier||'';
    el('setup-change-account').hidden=!accountUI.change;el('setup-change-account').disabled=busy;
    el('setup-first-connect').hidden=!accountUI.initialize;
    el('setup-bootstrap').disabled=busy||info.bootstrap.running||!info.account_ready||!el('setup-ack').checked;
    el('setup-check-wechat').hidden=!accountUI.verify;
    el('setup-check-wechat').disabled=busy||!info.keys_ready||info.bootstrap.running;
    el('setup-check-wechat').textContent=ready().wechat?'重新检查读取':connection.state==='initialized'?'验证读取并核对账号':'验证已有接入';
    el('setup-account-help').textContent=connection.credentials_saved?'为避免不同账号的聊天和读取凭据混用，已接入的账号在这里保持固定。当前版本更换账号需使用独立的 ChatSeek 数据目录重新接入。':'首次接入前可以重新选择数据。已经开始但未完成的接入目录需要保留检查，不能直接覆盖。';
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
  el('setup-save-account').onclick=()=>run(1,async()=>{await api('setup-account',{account:el('setup-account').value});choosingAccount=false;});
  el('setup-account').onchange=render;
  el('setup-change-account').onclick=()=>{choosingAccount=true;messages[1]='';render();};
  el('setup-refresh-accounts').onclick=()=>run(1,async()=>{});
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
