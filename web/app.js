'use strict';
const $ = id => document.getElementById(id);
let state = null, busy = false, view = 'ask', historyVersion = '';
let dialogLoaded = '';
let recentFilter='active', archivedTurns=[], deletedTurns=[], recentMenuTarget=null;
let accessMode='ask';
let accessReady;
const previews=new Map();
function nearChatBottom(){const el=$('conversation');return el.scrollHeight-el.scrollTop-el.clientHeight<90;}
function scrollChat(force=false){
  const follow=force||nearChatBottom();
  if(follow)requestAnimationFrame(()=>$('conversation').scrollTo({top:$('conversation').scrollHeight,behavior:'auto'}));
}
function resizeQuestion(){
  const input=$('question');input.style.height='auto';input.style.height=Math.min(input.scrollHeight,180)+'px';
}
const views = {ask:'问答',history:'聊天记录',search:'搜索原文'};
let noticeTimer;
function notice(text='',duration=0) {
  clearTimeout(noticeTimer);
  $('notice-text').textContent=text;
  $('notice').hidden=!text;
  if(text && duration>0)noticeTimer=setTimeout(()=>notice(),duration);
}
$('notice-close').addEventListener('click',()=>notice());
async function api(action, data) {
  if(data!==undefined && needsAccessConfirmation(action,data)){
    data=await authorizeAccess(action,data);
  }
  const response = await localRequest('/api/'+action, data === undefined ? {cache:'no-store'} : {method:'POST',headers:{'Content-Type':'application/json','X-WeChat-RAG':'1'},body:JSON.stringify(data)});
  let result;
  try {result=await response.json();} catch {throw new Error('网页服务连接异常，请确认终端中的服务仍在运行。');}
  if (!response.ok) {
    if(result.access_confirmation_required){accessMode='ask';renderAccessMode();}
    throw new Error((result.error || '操作失败，请重试。').replaceAll('微信','聊天客户端'));
  }
  return result;
}
function needsAccessConfirmation(action,data){
  return ['ask','select','sync','media-preview','media-recognize','media-import','rag-index'].includes(action) || (action==='watch' && data.enabled===true);
}
async function authorizeAccess(action,data){
  await accessReady;
  if(accessMode==='ask' && data.access_approved!==true){
    if(!await confirmAccess(action)){
      const error=new Error('已取消本次访问。');error.name='AccessCancelled';throw error;
    }
    return {...data,access_approved:true};
  }
  return data;
}
function renderAccessMode(){
  $('access-mode').value=accessMode;
  $('access-help-button').textContent=accessMode==='full'?'系统授权设置':'权限说明';
  $('access-mode').title=accessMode==='full'?'跳过应用内确认；macOS 系统权限仍需手动授权':'提问或访问原始聊天、媒体前，先确认本次访问';
}
function confirmAccess(action){
  const messages={
    'rag-index':'将在本机为当前会话建立语义向量索引，聊天不会发送至向量服务。首次运行需要下载公共模型。',
    ask:'将读取当前会话的聊天记录，并可能读取相关图片、视频或表情包。问题、相关记录和结合的历史问答会发送至你配置的模型服务；范围总结会发送范围内全部聊天文字。',
    select:'将查找本机联系人或群聊，并同步你选择的会话记录。',
    sync:'将读取当前会话在本机的原始聊天记录，更新本地索引。',
    watch:'将开启当前会话的持续同步，后台每 5 秒读取一次新消息，直到你暂停同步。',
    'media-preview':'将读取这条消息的本机媒体文件并生成预览，不调用模型。',
    'media-recognize':'将读取这条消息的媒体画面，发送至你配置的视觉模型服务识别。视频仅分析抽样画面。',
    'media-import':'将读取导入的媒体文件并保存本机预览，不调用模型。'
  };
  $('access-confirm-text').textContent=messages[action];
  const dialog=$('access-confirm');
  return new Promise(resolve=>{
    dialog.returnValue='cancel';
    dialog.addEventListener('close',()=>resolve(dialog.returnValue==='allow'),{once:true});
    dialog.showModal();
  });
}
accessReady=api('access').then(result=>{accessMode=result.mode;renderAccessMode();}).catch(error=>{
  accessMode='ask';renderAccessMode();notice('权限设置读取失败，已按询问请求处理：'+error.message);
});
$('access-mode').addEventListener('change',async()=>{
  if(busy){renderAccessMode();return;}
  const requested=$('access-mode').value;
  lock(true);
  try{
    accessMode=(await api('access',{mode:requested})).mode;renderAccessMode();
    if(accessMode==='full')$('access-help').showModal();
    else notice('已切换为询问请求。已开启的后台同步仍按原设置运行，可在同步详情中暂停。',5000);
  }catch(error){renderAccessMode();notice(error.message);}
  finally{lock(false);}
});
$('access-help-button').addEventListener('click',()=>$('access-help').showModal());
$('open-system-access').addEventListener('click',async()=>{
  const button=$('open-system-access');button.disabled=true;
  try{await api('access-settings',{});$('access-settings-status').textContent='已打开系统设置。请手动授权，再退出并重新启动获得授权的应用和网页服务。';}
  catch(error){$('access-settings-status').textContent=error.message;}
  finally{button.disabled=false;}
});
function lock(value) {
  busy=value;
  if(value)closeRecentMenu();
  document.querySelectorAll('#select-contact,#contact-name,#ask-button,#question,#search-button,#search-query,#sync-now,#watch-toggle,#new-dialog,#access-mode,#recent-filter,#retrieval-mode,#vector-rebuild,.recent-more,.candidate,.suggestions button,.media-action').forEach(el=>el.disabled=value);
}
async function operation(task) {
  if(busy)return;
  notice();lock(true);
  try{await task();}catch(error){notice(error.message,error.name==='AccessCancelled'?5000:0);}finally{lock(false);}
}
function adopt(next) {
  const changed=state && state.context!==next.context;
  state=next;
  renderVectorIndex(next.vector_index);
  $('side-contact').textContent=next.contact || '尚未选择';
  $('header-contact-name').textContent=next.contact || '选择一个会话';
  $('conversation-kind').textContent=next.conversation_type==='group'?'群聊 · 独立会话':'好友 · 独立会话';
  $('side-avatar').textContent=Array.from(next.contact || '·')[0];
  $('stat-contact').textContent=next.contact || '—';
  $('stat-messages').textContent=next.messages ?? '—';
  $('stat-text').textContent=next.text_messages ?? '—';
  $('stat-time').textContent=next.last_sync ? next.last_sync.slice(5,19).replace('T',' ') : '尚未同步';
  $('sync-time').textContent=$('stat-time').textContent;
  $('sync-time').dateTime=next.last_sync || '';
  $('sync-time').title='最近同步：'+(next.last_sync ? next.last_sync.replace('T',' ') : '尚未同步');
  const age=next.last_sync ? (Date.now()-Date.parse(next.last_sync))/1000 : Infinity;
  const healthy=next.watch_running && age<35;
  $('sync-state').textContent=!next.watch_running?'已暂停':healthy?'已同步':'同步延迟';
  $('sync-state').title=!next.watch_running?'自动同步已暂停':'每 5 秒自动同步，最近一次：'+($('stat-time').textContent);
  $('sync-state').classList.toggle('paused',!healthy);
  $('watch-toggle').textContent=next.watch_running?'暂停自动同步':'开启自动同步';
  $('model-note').textContent=next.api_configured?'模型请求直接发送到你配置的 API 地址。具体问题会发送相关原文；范围总结会发送范围内全部聊天文字和可用媒体描述，结合的历史问答也会发送至你配置的模型服务。每次最多识别 3 条媒体并发送画面，优先图片。视频仅分析抽样画面，语音尚不支持。':'请在“模型与接入配置”中配置自己的模型 API 地址、模型名和 Key。';
  if(changed) {
    closeRecentMenu();archivedTurns=[];deletedTurns=[];recentFilter='active';$('recent-filter').value='active';$('recent-title').textContent='最近提问';
    if($('turn-preview').open)$('turn-preview').close();
    document.body.classList.remove('has-conversation');
    previews.clear();
    $('conversation').replaceChildren();
    const empty=document.createElement('div');empty.className='empty';empty.textContent='已切换会话，可以开始新的提问。';$('conversation').append(empty);
    $('search-list').replaceChildren();$('search-query').value='';historyVersion='';
    $('question').value='';resizeQuestion();
    $('recent-questions').replaceChildren();
  }
  return changed;
}
async function refresh() {
  const next=await api('state');
  if(next.setup_required){
    if(state){
      $('conversation').textContent='请完成配置并选择一个会话。';
      for(const id of ['recent-questions','history-list','search-list'])$(id).replaceChildren();
      previews.clear();archivedTurns=[];deletedTurns=[];dialogLoaded='';
    }
    state=null;
    $('side-contact').textContent='请完成首次配置';
    $('header-contact-name').textContent='选择一个会话';
    $('sync-state').textContent='尚未配置';
    $('memory-status').textContent='配置后开始聊天';
    $('vector-status').textContent='下载模型并选择会话后建立索引';
    if(!window.chatseekSetupShown){window.chatseekSetupShown=true;window.showChatSeekSetup?.();}
    return;
  }
  const changed=adopt(next);
  if(dialogLoaded!==next.context+':'+next.dialog_id)await loadDialog();
  const version=next.context+':'+next.index_version+':'+$('history-limit').value;
  if(view==='history' && version!==historyVersion) await history();
  if(changed && view==='search')messages($('search-list'),[],'输入关键词，搜索当前会话的聊天。');
}
function sourceNode(row){
  const node=document.createElement('div');node.className='source';
  const meta=document.createElement('div');meta.className='source-meta';
  const id=document.createElement('span');id.className='source-id';id.textContent='['+row.id+']';
  meta.append(id,document.createTextNode(row.sender+' · '+row.time.slice(0,19).replace('T',' ')));
  const text=document.createElement('div');text.textContent=row.text;node.append(meta,text);mediaControls(node,row);return node;
}
function mediaControls(container,row){
  if(!row.media?.supported)return;
  const box=document.createElement('div');box.className='media-controls';
  const actions=document.createElement('div');actions.className='media-buttons';
  const preview=document.createElement('button');preview.type='button';preview.className='media-action';preview.textContent='加载预览 · 仅本机';
  const recognize=document.createElement('button');recognize.type='button';recognize.className='media-action recognize';recognize.textContent=row.media.recognized?'查看识别结果':'识别内容 · 模型服务';
  const imported=document.createElement('button');imported.type='button';imported.className='media-action';imported.textContent='导入已保存文件';
  const fileInput=document.createElement('input');fileInput.type='file';fileInput.accept='image/jpeg,image/png,image/gif,image/webp,video/mp4';fileInput.hidden=true;
  const status=document.createElement('div');status.className='media-status';
  const gallery=document.createElement('div');gallery.className='media-gallery';
  const description=document.createElement('div');description.className='media-description';
  const context=state?.context,key=context+':'+row.id;
  function show(result){
    gallery.replaceChildren();
    for(const frame of result.frames){const figure=document.createElement('figure'),image=document.createElement('img'),caption=document.createElement('figcaption');image.src=frame.url;image.alt=row.message_type+'预览，'+frame.label;image.loading='lazy';caption.textContent=frame.label;figure.append(image,caption);gallery.append(figure);}
    status.textContent=result.scope;
    description.textContent=result.description ? 'AI 视觉识别（'+result.model+'）：\n'+result.description : '预览在本机生成，尚未发送给模型。';
    if(result.description)recognize.textContent='查看识别结果';
  }
  async function run(action){
    status.textContent=action==='media-recognize'?'正在将所选画面发送给视觉模型服务 识别…':'正在读取本机媒体…';
    try{
      const result=await api(action,{context,id:row.id});
      if(result.context!==state.context)return;
      previews.set(key,result);show(result);
    }catch(error){status.textContent=error.message;throw error;}
  }
  preview.addEventListener('click',()=>operation(()=>run('media-preview')));
  recognize.addEventListener('click',()=>operation(()=>run('media-recognize')));
  imported.addEventListener('click',()=>fileInput.click());
  fileInput.addEventListener('change',()=>operation(async()=>{
    try {
    const file=fileInput.files[0];if(!file)return;
    if(file.size>20*1024*1024)throw new Error('请选择不超过 20 MB 的文件；大视频请先在聊天客户端中下载后尝试本地预览。');
    status.textContent='正在本机导入并生成预览，不会上传到模型…';
    const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(new Error('文件读取失败。'));reader.readAsDataURL(file);});
    const result=await api('media-import',{context,id:row.id,file:encoded});
    if(result.context===state.context){previews.set(key,result);show(result);}
    } catch(error) {
      status.textContent=error.message;throw error;
    } finally {fileInput.value='';}
  }));
  actions.append(preview,recognize,imported,fileInput);box.append(actions,status,gallery,description);container.append(box);
  if(previews.has(key))show(previews.get(key));
}
function messages(container,rows,emptyText='没有找到聊天记录。'){
  container.replaceChildren();
  if(!rows.length){const e=document.createElement('div');e.className='empty';e.textContent=emptyText;container.append(e);return;}
  rows.forEach(row=>{
    const node=document.createElement('article');node.className='message';
    const head=document.createElement('div');head.className='message-head';
    const name=document.createElement('strong');name.textContent=row.sender;
    const id=document.createElement('span');id.className='source-id';id.textContent='['+row.id+']';
    const time=document.createElement('time');time.textContent=row.time.slice(0,19).replace('T',' ');
    head.append(name,id);
    if(!row.content_recognized){const badge=document.createElement('span');badge.className='media-tag';badge.textContent=row.message_type+' · 未识别';head.append(badge);}
    head.append(time);const text=document.createElement('p');text.textContent=row.text;node.append(head,text);mediaControls(node,row);container.append(node);
  });
}
async function history(){
  if(!state)return;
  const context=state.context,limit=$('history-limit').value;
  const result=await api('history',{context,limit});
  if(state.context!==result.context)return;
  messages($('history-list'),result.sources);
  historyVersion=context+':'+state.index_version+':'+limit;
}
function formatAnswer(container,text){
  // Render a small safe subset of formatting. Never interpret model/chat HTML.
  for(const [index,line] of text.split('\n').entries()){
    if(index)container.append(document.createTextNode('\n'));
    const plain=line.replace(/^#{1,6}\s+/,'');
    plain.split(/(\*\*[^*]+\*\*)/g).forEach(part=>{
      if(part.startsWith('**') && part.endsWith('**')){const strong=document.createElement('strong');strong.textContent=part.slice(2,-2);container.append(strong);}
      else container.append(document.createTextNode(part));
    });
  }
}
function processingNote(container,report){
  if(!report)return;
  const lines=[];
  if(report.recognized.length)lines.push('已自动识别：'+report.recognized.join('、'));
  if(report.cached.length)lines.push('已复用媒体识别：'+report.cached.join('、'));
  for(const item of report.failed)lines.push(item.id+' 识别未完成：'+item.reason);
  if(report.deferred.length)lines.push('每次最多自动处理 '+report.limit+' 条；尚未处理：'+report.deferred.join('、')+'。可指定编号继续提问。');
  if(lines.length){const note=document.createElement('div');note.className='processing-note';note.textContent=lines.join('\n');container.append(note);}
}
function createTurn(question){
  document.body.classList.add('has-conversation');
  const turn=document.createElement('article');turn.className='turn';
  const prompt=document.createElement('div');prompt.className='question-bubble';prompt.textContent=question;
  const answer=document.createElement('div');answer.className='answer';turn.append(prompt,answer);
  return {turn,answer};
}
function renderResult(turn,answer,result){
  if(result.turn_id)turn.dataset.turnId=String(result.turn_id);
  answer.classList.remove('thinking');answer.replaceChildren();formatAnswer(answer,result.answer);
  if(result.query_context?.memory_turns){
    const note=document.createElement('div');note.className='memory-note';
    note.textContent='已结合前 '+result.query_context.memory_turns+' 轮问答理解本次问题';turn.append(note);
  }
  if(result.query_context?.date_range || result.query_context?.time_scope==='all_history' || result.query_context?.retrieval_mode==='full_summary'){
    const scope=document.createElement('div');scope.className='processing-note query-scope';
    const context=result.query_context,report=context.summary_processing;
    const range=context.date_range?'查询日期：'+context.date_range.display:'查询范围：当前会话全部已同步历史（不限日期）';
    scope.textContent=range+'\n'+(context.retrieval_mode==='full_summary'
      ? '已纳入全部 '+context.selected_messages+' 条聊天'+(report?.mode==='batched'?'，分 '+report.batches+' 批阅读后汇总':'，一次完整提交')+'（不含系统消息）。'+(report?.unrecognized_messages?'其中 '+report.unrecognized_messages+' 条非文字消息内容尚未识别。':'')
      : '本次检索 '+context.selected_messages+' 条相关原文；这个范围内已同步 '+context.available_messages+' 条聊天。');turn.append(scope);
  }
  if(result.query_context?.retrieval){
    const report=result.query_context.retrieval,note=document.createElement('div');note.className='processing-note';
    note.textContent=(report.mode==='hybrid'?'混合检索':'纯向量检索')+' · 检索范围内 '+report.eligible_chunks+' 个语义片段，选用 '+report.selected_chunks+' 个'+(report.retried?' · 已补充检索一次':'');turn.append(note);
  }
  processingNote(turn,result.media_processing);
  const details=document.createElement('details');details.className='sources';
  const summary=document.createElement('summary');summary.textContent='查看 '+result.sources.length+' 条原文依据';details.append(summary);
  result.sources.forEach(row=>details.append(sourceNode(row)));turn.append(details);
  const actions=document.createElement('div');actions.className='answer-actions';
  const copy=document.createElement('button');copy.type='button';copy.className='copy-answer';copy.setAttribute('aria-label','复制回答');copy.title='复制回答';
  const icon=document.createElementNS('http://www.w3.org/2000/svg','svg');icon.classList.add('icon');icon.setAttribute('aria-hidden','true');
  const use=document.createElementNS('http://www.w3.org/2000/svg','use');use.setAttribute('href','#i-copy');icon.append(use);
  const copied=document.createElement('span');copy.append(icon,copied);
  copy.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(result.answer);copied.textContent='已复制';setTimeout(()=>copied.textContent='',1600);}catch{notice('复制未完成，请选中回答文字复制。');}});
  actions.append(copy);turn.append(actions);
}
function rebuildRecent(){
  closeRecentMenu();
  const list=$('recent-questions');list.replaceChildren();
  $('recent-title').textContent={active:'最近提问',archived:'已归档',deleted:'最近删除'}[recentFilter];
  const entries=recentFilter==='active'
    ? Array.from($('conversation').querySelectorAll('.turn[data-turn-id]')).reverse().map(turn=>({turn_id:Number(turn.dataset.turnId),question:turn.querySelector('.question-bubble').textContent,turn}))
    : [...(recentFilter==='archived'?archivedTurns:deletedTurns)].reverse();
  for(const entry of entries){
    const row=document.createElement('div');row.className='recent-row';
    const button=document.createElement('button');button.className='recent-question';button.type='button';button.textContent=entry.question;button.title=entry.question;
    button.addEventListener('click',()=>{
      if(recentFilter!=='active'){
        $('turn-preview-title').textContent=entry.question;$('turn-preview-answer').replaceChildren();formatAnswer($('turn-preview-answer'),entry.answer);
        $('turn-preview-note').textContent=recentFilter==='archived'?'已归档 · 可在操作菜单中取消归档；归档记录不参与后续问答。':'最近删除 · 可在操作菜单中恢复；原始聊天记录未被删除。';
        $('turn-preview').showModal();return;
      }
      switchView('ask');if(matchMedia('(max-width:720px)').matches)setSidebar(false);const container=$('conversation');requestAnimationFrame(()=>container.scrollTo({top:container.scrollTop+entry.turn.getBoundingClientRect().top-container.getBoundingClientRect().top-16,behavior:'auto'}));
    });
    const more=document.createElement('button');more.type='button';more.className='recent-more';more.textContent='⋯';more.setAttribute('aria-label','提问操作：'+entry.question);more.setAttribute('aria-haspopup','menu');more.setAttribute('aria-expanded','false');more.title='提问操作';more.disabled=busy;
    more.addEventListener('click',()=>openRecentMenu(entry,more));
    row.addEventListener('contextmenu',event=>{event.preventDefault();openRecentMenu(entry,more,{x:event.clientX,y:event.clientY});});
    row.addEventListener('keydown',event=>{if(event.key==='ContextMenu' || (event.shiftKey && event.key==='F10')){event.preventDefault();openRecentMenu(entry,more);}});
    row.append(button,more);list.append(row);
  }
  if(!entries.length){const empty=document.createElement('p');empty.className='recent-empty';empty.textContent={active:'开始对话后，会显示在这里',archived:'暂无归档记录',deleted:'暂无删除记录'}[recentFilter];list.append(empty);}
}
function closeRecentMenu(focus=false){
  const trigger=recentMenuTarget?.trigger;
  if(trigger)trigger.setAttribute('aria-expanded','false');
  $('recent-menu').hidden=true;recentMenuTarget=null;
  if(focus && trigger?.isConnected)trigger.focus();
}
function openRecentMenu(entry,trigger,point){
  if(busy)return;
  closeRecentMenu();
  recentMenuTarget={entry,trigger,context:state.context,session:state.dialog_id};
  trigger.setAttribute('aria-expanded','true');
  const menu=$('recent-menu');menu.replaceChildren();
  const actions=recentFilter==='active'?[['archive','归档','i-archive'],['delete','删除','i-trash']]
    : recentFilter==='archived'?[['unarchive','取消归档','i-restore'],['delete','删除','i-trash']]:[['restore','恢复','i-restore']];
  for(const [action,label,symbol] of actions){
    const button=document.createElement('button');button.type='button';button.setAttribute('role','menuitem');button.className=action==='delete'?'danger':'';
    const icon=document.createElementNS('http://www.w3.org/2000/svg','svg');icon.classList.add('icon');icon.setAttribute('aria-hidden','true');const use=document.createElementNS('http://www.w3.org/2000/svg','use');use.setAttribute('href','#'+symbol);icon.append(use);button.append(icon,document.createTextNode(label));
    button.addEventListener('click',()=>{
      const target=recentMenuTarget;closeRecentMenu();
      operation(async()=>{
        const result=await api('dialog-manage',{context:target.context,session:target.session,turn_id:target.entry.turn_id,action});
        if(result.context!==state.context || result.dialog_id!==state.dialog_id)return;
        renderDialog(result,{preserveScroll:true});
        notice({archive:'已归档，可在“归档”分类查看。',unarchive:'已恢复到最近提问。',delete:'已移至最近删除，可切换分类恢复。',restore:'已恢复，可在最近提问或归档中查看。'}[action],5000);
      });
    });menu.append(button);
  }
  menu.hidden=false;
  const rect=trigger.getBoundingClientRect(),bounds=menu.getBoundingClientRect();
  menu.style.left=Math.max(8,Math.min(point?.x ?? rect.right-bounds.width,innerWidth-bounds.width-8))+'px';
  menu.style.top=Math.max(8,Math.min(point?.y ?? rect.bottom+5,innerHeight-bounds.height-8))+'px';
  menu.querySelector('button').focus();
}
$('recent-filter').addEventListener('change',()=>{recentFilter=$('recent-filter').value;rebuildRecent();});
$('recent-questions').addEventListener('scroll',()=>closeRecentMenu(),{passive:true});
document.addEventListener('pointerdown',event=>{if(recentMenuTarget && !$('recent-menu').contains(event.target) && !recentMenuTarget.trigger.contains(event.target))closeRecentMenu();});
document.addEventListener('keydown',event=>{
  if(!recentMenuTarget)return;
  if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();closeRecentMenu(true);return;}
  if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){
    event.preventDefault();const buttons=[...$('recent-menu').querySelectorAll('button')],index=buttons.indexOf(document.activeElement);
    buttons[event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length].focus();
  }
});
addEventListener('resize',()=>closeRecentMenu());
function renderDialog(result,{preserveScroll=false}={}){
  archivedTurns=result.archived_turns || [];deletedTurns=result.deleted_turns || [];
  const oldScroll=$('conversation').scrollTop;
  const container=$('conversation');container.replaceChildren();
  document.body.classList.toggle('has-conversation',result.turns.length>0);
  for(const row of result.turns){const {turn,answer}=createTurn(row.question);renderResult(turn,answer,row);container.append(turn);}
  if(!result.turns.length){const empty=document.createElement('div');empty.className='empty';const title=document.createElement('h1');title.textContent='从一句提问开始';const text=document.createElement('p');text.textContent='查找聊过的信息，接着聊也可以。';empty.append(title,text);container.append(empty);}
  $('memory-status').textContent='可连续追问 · 问答保存在本机';
  rebuildRecent();
  if(preserveScroll)container.scrollTop=oldScroll;else scrollChat(true);
}
async function loadDialog(){
  const context=state.context,session=state.dialog_id;
  const result=await api('dialog-history',{context,session});
  if(context!==state.context || session!==state.dialog_id)return;
  renderDialog(result);dialogLoaded=context+':'+session;
}
$('new-dialog').addEventListener('click',()=>operation(async()=>{
  const result=await api('dialog-new',{context:state.context,session:state.dialog_id});
  if(result.context!==state.context)return;
  state.dialog_id=result.dialog_id;dialogLoaded=state.context+':'+result.dialog_id;
  recentFilter='active';$('recent-filter').value='active';
  renderDialog(result);$('question').value='';resizeQuestion();await switchView('ask');if(matchMedia('(max-width:720px)').matches)setSidebar(false);notice('已开始新对话，后续提问不再携带之前的问答。');
}));
async function choose(name,id){
  const result=await api('select',{name,id});
  $('contact-feedback').textContent=result.message;$('candidates').replaceChildren();
  if(result.candidates)result.candidates.forEach(row=>{
    const button=document.createElement('button');button.type='button';button.className='candidate';button.textContent=(row.conversation_type==='group'?'[群聊] ':'[好友] ')+(row.remark || row.nickname || row.id)+' · '+row.id;
    button.addEventListener('click',()=>operation(()=>choose(name,row.id)));$('candidates').append(button);
  });
  if(result.selected){adopt(result);$('contact-name').value='';await refresh();}
}
async function switchView(next){
  view=next;
  document.body.classList.toggle('chat-view',view==='ask');
  document.querySelectorAll('[data-view]').forEach(el=>{const active=el.dataset.view===view;el.classList.toggle('active',active);if(active)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current');});
  for(const key of Object.keys(views))$(key+'-panel').hidden=key!==view;
  $('view-label').textContent=views[view];
  if(view==='ask')scrollChat(true);
  if(view==='history')try{await history();}catch(error){notice(error.message);}
}
document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>{switchView(button.dataset.view);if(matchMedia('(max-width:720px)').matches)setSidebar(false);}));
$('contact-form').addEventListener('submit',event=>{event.preventDefault();operation(()=>choose($('contact-name').value));});
$('sync-now').addEventListener('click',()=>operation(async()=>{const result=await api('sync',{context:state?.context});adopt(result);notice('同步完成，本轮新增或更新 '+result.changed_messages+' 条消息。',5000);if(view==='history')await history();}));
$('watch-toggle').addEventListener('click',()=>operation(async()=>adopt(await api('watch',{enabled:!state?.watch_running}))));
$('history-limit').addEventListener('change',()=>operation(history));
$('search-form').addEventListener('submit',event=>{event.preventDefault();operation(async()=>{
  const result=await api('search',{context:state?.context,question:$('search-query').value});
  if(result.context===state.context){messages($('search-list'),result.sources,'没有找到相关记录，试试其他关键词。');}
});});
$('ask-form').addEventListener('submit',event=>{event.preventDefault();operation(async()=>{
  if(!state)throw new Error('请先在“模型与接入配置”完成初始化，再选择一个会话。');
  if(!state.api_configured)throw new Error('请在“模型与接入配置”配置自己的模型 API 地址、模型名和 Key。');
  if(dialogLoaded!==state.context+':'+state.dialog_id)throw new Error('对话仍在恢复，请稍后重试。');
  const question=$('question').value.trim();if(!question)return;
  const context=state.context,session=state.dialog_id;
  const request=await authorizeAccess('ask',{context,session,question,retrieval_mode:$('retrieval-mode').value});
  const container=$('conversation');if(container.querySelector('.welcome,.empty'))container.replaceChildren();
  const {turn,answer}=createTurn(question);
  answer.classList.add('thinking');answer.textContent='正在读取聊天并整理答案，范围较大时会分批阅读，请稍候…';container.append(turn);scrollChat(true);
  const progress=document.createElement('div');progress.className='stream-status';progress.setAttribute('role','status');turn.append(progress);
  answer.setAttribute('aria-busy','true');
  let streamed='',paint=null;
  const paintAnswer=()=>{
    paint=null;const follow=nearChatBottom();
    answer.classList.remove('thinking');answer.classList.add('streaming');answer.replaceChildren();formatAnswer(answer,streamed);
    if(follow)scrollChat(true);
  };
  try{
    const response=await localRequest('/api/ask',{method:'POST',headers:{'Content-Type':'application/json','X-WeChat-RAG':'1'},body:JSON.stringify({...request,stream:true})});
    if(!response.ok){
      let result;try{result=await response.json();}catch{throw new Error('网页服务连接异常，请确认本机服务仍在运行。');}
      if(result.access_confirmation_required){accessMode='ask';renderAccessMode();}
      throw new Error((result.error || '提问失败，请重试。').replaceAll('微信','聊天客户端'));
    }
    const result=await readChatStream(response,event=>{
      if(event.type==='reset'){
        if(paint)cancelAnimationFrame(paint);paint=null;streamed='';answer.classList.remove('streaming');answer.classList.add('thinking');answer.textContent=event.message;progress.textContent='';
      }else if(event.type==='status'){
        const message=String(event.message || '').replaceAll('微信','聊天客户端');
        if(!streamed)answer.textContent=message;
        else progress.textContent=message;
      }else if(event.type==='delta'){
        streamed+=event.text;
        if(!paint)paint=requestAnimationFrame(paintAnswer);
      }
    });
    if(paint)cancelAnimationFrame(paint);paint=null;
    progress.remove();answer.classList.remove('streaming');answer.removeAttribute('aria-busy');
    if(result.context!==state.context || result.dialog_id!==state.dialog_id)return;
    const follow=nearChatBottom();
    renderResult(turn,answer,result);
    rebuildRecent();
    $('question').value='';resizeQuestion();if(follow)scrollChat(true);
  }catch(error){
    if(paint)cancelAnimationFrame(paint);
    const follow=nearChatBottom();answer.classList.remove('thinking','streaming');answer.removeAttribute('aria-busy');
    if(streamed){
      answer.replaceChildren();formatAnswer(answer,streamed);
      progress.className='processing-note streaming-error';progress.textContent='回答未完成，以上为临时内容，请勿当作完整结论。'+error.message+' 输入框已保留，可重试。';
    }else{progress.remove();answer.textContent='这次提问未完成：'+error.message;}
    if(follow)scrollChat(true);throw error;
  }
});});
$('question').addEventListener('input',resizeQuestion);
$('question').addEventListener('keydown',event=>{if(event.key==='Enter' && !event.shiftKey && !event.isComposing){event.preventDefault();$('ask-form').requestSubmit();}});
document.querySelectorAll('[data-question]').forEach(button=>button.addEventListener('click',()=>{$('question').value=button.dataset.question;resizeQuestion();$('question').focus();}));
refresh().catch(error=>notice(error.message));
setInterval(()=>{if(!busy)refresh().catch(error=>notice('连接中断：'+error.message));},5000);

// The companion is a separate movable overlay; it never enters the message flow.
function sidebarAccess(){
  const mobile=matchMedia('(max-width:720px)').matches;
  const open=mobile?document.body.classList.contains('sidebar-open'):!document.body.classList.contains('sidebar-collapsed');
  $('sidebar').inert=!open;$('sidebar-open').setAttribute('aria-expanded',String(open));
}
function setSidebar(open){
  const mobile=matchMedia('(max-width:720px)').matches;
  if(mobile)document.body.classList.toggle('sidebar-open',open);
  else document.body.classList.toggle('sidebar-collapsed',!open);
  sidebarAccess();
}
$('sidebar-open').addEventListener('click',()=>setSidebar(true));
$('sidebar-close').addEventListener('click',()=>{setSidebar(false);$('sidebar-open').focus();});
$('sidebar-scrim').addEventListener('click',()=>setSidebar(false));
$('header-contact').addEventListener('click',()=>{setSidebar(true);$('contact-name').focus();});
addEventListener('keydown',event=>{if(event.key==='Escape' && matchMedia('(max-width:720px)').matches){setSidebar(false);$('sidebar-open').focus();}});
addEventListener('resize',sidebarAccess);sidebarAccess();
let petDrag=null,petMoved=false;
const pet=$('pet'),petGrip=$('pet-grip');
function placePet(left,top){
  const rect=pet.getBoundingClientRect();
  pet.style.left=Math.max(8,Math.min(left,innerWidth-rect.width-8))+'px';
  pet.style.top=Math.max(64,Math.min(top,Math.max(64,innerHeight-rect.height-12)))+'px';
  pet.style.right='auto';petMoved=true;
}
petGrip.addEventListener('pointerdown',event=>{
  if(event.button!==0)return;
  const rect=pet.getBoundingClientRect();petDrag={x:event.clientX-rect.left,y:event.clientY-rect.top,id:event.pointerId};
  petGrip.setPointerCapture(event.pointerId);pet.classList.add('dragging');
});
petGrip.addEventListener('pointermove',event=>{if(petDrag && petDrag.id===event.pointerId)placePet(event.clientX-petDrag.x,event.clientY-petDrag.y);});
function endPetDrag(){petDrag=null;pet.classList.remove('dragging');}
petGrip.addEventListener('pointerup',endPetDrag);petGrip.addEventListener('pointercancel',endPetDrag);petGrip.addEventListener('lostpointercapture',endPetDrag);
petGrip.addEventListener('keydown',event=>{const delta={ArrowLeft:[-16,0],ArrowRight:[16,0],ArrowUp:[0,-16],ArrowDown:[0,16]}[event.key];if(!delta)return;event.preventDefault();const rect=pet.getBoundingClientRect();placePet(rect.left+delta[0],rect.top+delta[1]);});
const petPreference='chatseek.pet.enabled';
function setPetEnabled(enabled,save=true){
  pet.hidden=!enabled;
  $('pet-toggle').setAttribute('aria-checked',String(enabled));
  $('pet-toggle').title=enabled?'关闭萌宠':'开启萌宠';
  if(save)try{localStorage.setItem(petPreference,String(enabled));}catch{/* Storage may be disabled; the switch still works. */}
}
let petEnabled=true;try{petEnabled=localStorage.getItem(petPreference)!=='false';}catch{}
setPetEnabled(petEnabled,false);
$('pet-toggle').addEventListener('click',()=>setPetEnabled($('pet-toggle').getAttribute('aria-checked')!=='true'));
addEventListener('storage',event=>{if(event.key===petPreference)setPetEnabled(event.newValue!=='false',false);});
addEventListener('resize',()=>{if(petMoved && !pet.hidden){const rect=pet.getBoundingClientRect();placePet(rect.left,rect.top);}});

// Retrieval preferences remain local to this browser; every request carries it.
try{if(localStorage.getItem('chatseek-retrieval-mode')==='hybrid')$('retrieval-mode').value='hybrid';}catch{}
$('retrieval-mode').addEventListener('change',()=>{try{localStorage.setItem('chatseek-retrieval-mode',$('retrieval-mode').value);}catch{}});
function renderVectorIndex(index){
  if(!index)return;
  const text=$('vector-status');
  text.textContent=index.ready?'语义索引已就绪 · '+index.chunks+' 个片段':index.error?index.error:index.running?'正在建立语义索引'+(index.total?' · '+index.processed+'/'+index.total:'…'):'语义索引待更新';
  text.title='本地 BGE 向量模型；纯向量模式不使用关键词预筛选';
}
$('vector-rebuild').addEventListener('click',()=>operation(async()=>{await api('rag-index',{context:state?.context});notice('已安排本地索引更新，可以继续查看历史问答。',5000);}));
