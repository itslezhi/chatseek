'use strict';
(() => {
  const modal=document.createElement('dialog');modal.id='setup-dialog';modal.className='setup-dialog';
  modal.innerHTML=`<form method="dialog" class="setup-heading"><h1>ChatSeek 模型与接入配置</h1><button aria-label="关闭">×</button></form>
  <p>Apple Silicon Mac 本地运行。聊天在本机保存和检索，云端 AI 会接收本次问题、聊天材料及历史问答；完整范围总结可能发送该范围全部文字。</p>
  <p id="setup-environment"></p><p id="setup-directory" class="setup-muted"></p>
  <section><h2>1 · 微信账号与初始化</h2><p>请先在原版微信登录一次。仅在你点击初始化后，程序才会复制微信、重签临时副本并用 LLDB 启动，验证密钥后保存本机。原版签名不变。需要 Xcode Command Line Tools；按系统提示为 ChatSeek 授权文件访问。</p>
  <label>本机账号 <select id="setup-account"></select></label><button id="setup-save-account" type="button">选择账号</button>
  <p><label><input id="setup-ack" type="checkbox">我已正常退出微信，理解并同意临时副本和调试初始化流程。</label></p>
  <button id="setup-bootstrap" type="button">初始化微信读取</button><button id="setup-permission" type="button">打开系统权限设置</button><p id="setup-bootstrap-status"></p></section>
  <section><h2>2 · 配置自己的大模型</h2><p>使用你自己的 API Key，费用由你与模型服务商结算。问题、聊天材料、历史问答及按需媒体画面直接发送到你填写的 API 地址。兼容 Chat Completions 协议，服务商还需支持结构化规划及所选的流式/视觉能力。</p>
  <label>API Base URL <input id="setup-base-url" type="url" placeholder="https://api.deepseek.com" autocomplete="url"></label>
  <label>文本模型 ID <input id="setup-text-model" placeholder="deepseek-flash" autocomplete="off"></label>
  <label>API Key <input id="setup-key" type="password" autocomplete="off" placeholder="重新填写自己的 Key；不会回显已保存的密钥"></label>
  <label>视觉模型 ID（可选，不填则仅处理文字）<input id="setup-vision-model" autocomplete="off" placeholder="填写支持图片输入的模型 ID"></label>
  <details><summary>上下文与兼容选项</summary><label>上下文容量（tokens）<input id="setup-context-tokens" type="number" min="1024" max="1000000" value="128000"></label><label>回答输出上限（tokens）<input id="setup-output-tokens" type="number" min="128" max="32768" value="8192"></label><label><input id="setup-json-mode" type="checkbox" checked>请求 JSON 输出格式（服务商不支持时可取消，模型仍需输出有效 JSON）</label></details>
  <button id="setup-save-key" type="button">保存模型配置</button><p id="setup-model-status"></p><p>密钥保存在本机权限受限的 .env 文件中，不会上传到项目作者的服务器。此配置文件没有额外加密，请勿分享。</p></section>
  <section><h2>3 · 本地向量模型</h2><p>首次下载 BAAI/bge-small-zh-v1.5 公共模型，约百 MB，需要联网；聊天向量在本机 CPU 计算。</p><button id="setup-model" type="button">下载 / 检查模型</button></section>
  <section><h2>4 · 选择会话</h2><p>密钥就绪后关闭此窗口，在左侧输入好友完整备注、昵称或群名并选择，完成首次同步。</p><p>关闭网页不会退出应用。请用 Dock 的“退出 ChatSeek”停止后台和自动同步。数据包含原文、媒体缓存和问答，使用普通 SQLite；文件权限限制不等于加密。</p></section>
  <p id="setup-feedback" role="status"></p><button id="setup-done" type="button">返回聊天界面</button>`;
  document.body.append(modal);
  const button=document.createElement('button');button.type='button';button.className='text-button';button.textContent='模型与接入配置';
  document.querySelector('.sidebar-bottom').before(button);
  const el=id=>document.getElementById(id);
  let timer;
  async function load(){
    const info=await api('setup');
    const config=info.model_config;
    if(config && !modal.dataset.modelLoaded){
      el('setup-base-url').value=config.base_url;el('setup-text-model').value=config.model;
      el('setup-vision-model').value=config.vision_model;el('setup-context-tokens').value=config.context_tokens;
      el('setup-output-tokens').value=config.max_output_tokens;el('setup-json-mode').checked=config.json_mode;
      modal.dataset.modelLoaded='1';
    }
    el('setup-model-status').textContent=info.api_configured?'已保存模型密钥；密钥内容不会显示。':'尚未配置模型密钥。';
    el('setup-environment').textContent=(info.supported?'Apple Silicon 环境':'此环境不支持微信初始化')+' · 微信 '+(info.wechat_version||'未检测到')+' · 已验证版本 '+info.tested_wechat_version+'（其他版本需实测）';
    el('setup-directory').textContent='本机数据目录：'+info.data_dir;
    const select=el('setup-account'),previous=select.value;select.replaceChildren();
    for(const account of info.accounts){const option=document.createElement('option');option.value=account.id;option.textContent=account.label;select.append(option);}
    if(previous)select.value=previous;
    el('setup-save-account').disabled=info.contact_ready||!info.accounts.length;
    el('setup-bootstrap').disabled=info.bootstrap.running||info.keys_ready||!info.account_ready||!info.supported;
    el('setup-bootstrap-status').textContent=info.bootstrap.message||(info.keys_ready?'已找到本机密钥。':!info.accounts.length?'未发现账号。请确认原版微信已登录，并检查系统文件访问权限。':'尚未初始化。');
    if(info.bootstrap.running)timer=setTimeout(()=>load().catch(error=>feedback(error.message)),2500);
  }
  function feedback(value){el('setup-feedback').textContent=value;}
  async function run(task){
    if(modal.dataset.busy)return;modal.dataset.busy='1';feedback('正在处理，请等待…');
    try{await task();await load();}catch(error){feedback(error.message);}finally{delete modal.dataset.busy;}
  }
  window.showChatSeekSetup=async()=>{if(!modal.open)modal.showModal();await load().catch(error=>feedback(error.message));};
  button.addEventListener('click',window.showChatSeekSetup);
  modal.addEventListener('close',()=>clearTimeout(timer));
  el('setup-done').onclick=()=>{modal.close();refresh().catch(error=>notice(error.message));};
  el('setup-save-account').onclick=()=>run(async()=>{await api('setup-account',{account:el('setup-account').value});feedback('账号已选择，请正常退出微信，然后初始化。');});
  el('setup-bootstrap').onclick=()=>run(async()=>{await api('setup-bootstrap',{acknowledged:el('setup-ack').checked});feedback('初始化已启动，请留意临时微信窗口。');});
  el('setup-permission').onclick=()=>run(async()=>{await api('access-settings',{});feedback('请为 ChatSeek 手动授权。授权后退出并重启 ChatSeek。');});
  el('setup-save-key').onclick=()=>run(async()=>{
    await api('setup-api',{key:el('setup-key').value,base_url:el('setup-base-url').value,model:el('setup-text-model').value,
      vision_model:el('setup-vision-model').value,context_tokens:Number(el('setup-context-tokens').value),
      max_output_tokens:Number(el('setup-output-tokens').value),json_mode:el('setup-json-mode').checked});
    el('setup-key').value='';feedback('已保存个人模型配置。请求将直连你配置的 API 地址。');
  });
  el('setup-model').onclick=()=>run(async()=>{await api('setup-model',{});feedback('本地模型已就绪。');});
  if(window.chatseekSetupShown)window.showChatSeekSetup();
})();
