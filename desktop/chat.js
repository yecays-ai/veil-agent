// Desktop IPC sends authenticated requests through the existing local Agent.
const $=id=>document.getElementById(id), col=$('col'), log=$('log'), box=$('box'), sendBtn=$('send');
const imageFile=$('image-file'),imageButton=$('image-button'),imagePreview=$('image-preview'),composer=$('composer');
const voiceHold=$('voice-hold');
const speechButton=$('speech-toggle'), speechStatus=$('speech-status');
const speechDraftKey='veil-speech-draft';
let speechRecorder=null,speechStream=null,speechChunks=[],speechSession=null,speechState='idle',speechPermissionPending=false,speechCancelled=false,speechDraftActive=false,speechTimer=null,speechErrorTimer=null;
let after=0, busy=true, transcribingVoice=false, stopping=false, stopped=false, typing=null, sending=false;
let revealQueue=[], revealTimer=null, roundStartedAt=0, roundHasReply=false, previousMessage=null;
let pendingApproval=null, pendingApprovalItems=[], pendingApprovalAt=0, activityEnabled=false, codexWatchEnabled=false, exiting=false;
let pollFailures=0;
const recentErrors=new Map();
const displayedErrorRequests=new Set();
const displayedEventIds=new Set();
let memoryAvailable=false,memoryLoaded=false,memoryLoading=false,memoryCursor='',memoryItems=[],memoryGroup='recent',memoryShown=50;
// Display-only groups over Ombre's own categories; Ombre's files keep their tags and categories.
const MEMORY_GROUPS={recent:{label:'最近',domains:null},events:{label:'经历',domains:null,events:true},us:{label:'我们俩',domains:['恋爱','人际']},peter:{label:'他自己',domains:['内心','心理','兴趣','游戏']},system:{label:'系统和能力',domains:['数字','AI','编程','工作']},archived:{label:'已归档',domains:null,archived:true}};
let eventItems=[];
let pendingQuote=null;
let quietEnabled=false;
let activityFilter='all',toolActivity=[],autonomyActivity=[],glanceActivity=[],detachedViews=new Set();
const activityRowNodes=new Map();
const pageQuery=new URLSearchParams(location.search),detachedView=pageQuery.get('detached')==='1'?pageQuery.get('view'):null;
const canLoadChatMedia=!detachedView||detachedView==='chat';
let selectedImage=null;const awaitingImages=new Map();
let voiceRecorder=null,voiceStream=null,voiceChunks=[],voiceStarted=0,voiceTimer=null,voiceCancelled=false,voiceSending=false,voicePermissionPending=false;
const unsentVoiceBlobs=new Map();
let imageChoice=0;
const draftRequests=new Map();
const IMAGE_TYPES=new Set(['image/png','image/jpeg','image/gif','image/webp']);
const GROUP_GAP=15*60*1000;
// 工单 392：同一句系统提示只留一条。QQ 通道失败时后端每轮都发同一句，以前一条条堆在对话里。
const NOTICE_REPEAT_MS=6*60*60*1000;
const recentNotices=new Map();
function speechIsActive(){return speechState==='listening'||speechState==='processing'||!!speechRecorder||speechPermissionPending}
const TOAST_LIMIT=3,TOAST_REPEAT_MS=30000;
function notify(text,kind='notice'){
  const box=$('toasts'),now=Date.now();
  // 工单 392：同一句只弹一次，不再连刷十几条；也不显示计数。
  if(box.lastElementChild?.dataset.text===text&&now-Number(box.lastElementChild.dataset.at||0)<TOAST_REPEAT_MS)return;
  const node=document.createElement('div');node.className=kind==='error'?'toast error':'toast';
  node.dataset.text=text;node.dataset.at=String(now);node.textContent=text;
  while(box.children.length>=TOAST_LIMIT)box.firstElementChild.remove();
  box.appendChild(node);
  setTimeout(()=>node.remove(),Math.min(15000,Math.max(2000,text.length*80)));
}
function setPage(name){
  if(!['chat','activity','context','instructions','memory','mcp','more','life','peter'].includes(name))return;
  if(detachedView&&name!==detachedView)return;
  document.body.dataset.currentPage=name;
  document.querySelectorAll('[data-page]').forEach(button=>{
    const active=button.dataset.page===name;button.classList.toggle('active',active);
    if(active)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');
  });
  document.querySelectorAll('[data-page-view]').forEach(page=>{page.hidden=page.dataset.pageView!==name});
}
function saveSpeechDraft(value){try{sessionStorage.setItem(speechDraftKey,value)}catch(e){}}
function resizeBox(){box.style.height='auto';box.style.height=Math.min(box.scrollHeight,200)+'px'}
function renderSpeechText(finalText){
  const prefix=speechSession.base;
  const separator=prefix&&/^[A-Za-z0-9]/.test(finalText)&&/[A-Za-z0-9]$/.test(prefix)?' ':'';
  box.value=prefix+separator+finalText;
  resizeBox();
  if(finalText)saveSpeechDraft(prefix+separator+finalText);
}
function setSpeechStatus(state,message){
  if(speechErrorTimer){clearTimeout(speechErrorTimer);speechErrorTimer=null}
  speechState=state;speechStatus.dataset.state=state;speechStatus.textContent=message;
  const active=speechIsActive();
  speechButton.setAttribute('aria-pressed',String(active));
  speechButton.setAttribute('aria-label',active?'停止语音识别':'开始语音识别');
  speechButton.title=active?'停止语音识别':'开始语音识别';
  speechButton.innerHTML=window.veilIcon('captions',16);
  box.disabled=active;
  sendBtn.disabled=sending||stopping||stopped||active;
  if(state==='error')speechErrorTimer=setTimeout(()=>{if(speechState==='error')setSpeechStatus('idle','')},3000);
}
try{
  const savedSpeechDraft=sessionStorage.getItem(speechDraftKey);
  if(savedSpeechDraft!==null){box.value=savedSpeechDraft;speechDraftActive=true;resizeBox()}
}catch(e){}
if(!navigator.mediaDevices?.getUserMedia||typeof MediaRecorder==='undefined'){
  speechButton.disabled=true;
  setSpeechStatus('error','无法录音，检查麦克风权限');
}
function speechMime(){
  for(const mime of ['audio/webm;codecs=opus','audio/webm','audio/ogg;codecs=opus','audio/mp4'])if(MediaRecorder.isTypeSupported?.(mime))return mime;
  return '';
}
function stopSpeechTracks(){if(speechStream){speechStream.getTracks().forEach(track=>track.stop());speechStream=null}}
function stopSpeech(cancel=false){
  speechCancelled=speechCancelled||cancel;
  if(speechTimer){clearTimeout(speechTimer);speechTimer=null}
  if(speechRecorder&&!cancel)setSpeechStatus('processing','录音结束，转写中…');
  if(speechRecorder&&speechRecorder.state!=='inactive')speechRecorder.stop();
  else if(speechPermissionPending){speechPermissionPending=false;speechCancelled=true;speechSession=null;setSpeechStatus('idle','已取消录音。')}
}
async function finishSpeech(){
  const recorder=speechRecorder;speechRecorder=null;stopSpeechTracks();
  if(speechTimer){clearTimeout(speechTimer);speechTimer=null}
  if(speechCancelled){speechCancelled=false;speechChunks=[];speechSession=null;setSpeechStatus('idle','已取消录音。');return}
  const mime=(recorder?.mimeType||speechChunks[0]?.type||'audio/webm').split(';')[0];
  const blob=new Blob(speechChunks,{type:mime});speechChunks=[];
  if(!blob.size){speechSession=null;setSpeechStatus('error','没录到声音，重试');return}
  if(blob.size>10*1024*1024){speechSession=null;setSpeechStatus('error','录音超过 10 MB，缩短后重试');return}
  setSpeechStatus('processing','转写中…');
  try{
    const dataUrl=await fileAsDataUrl(blob),comma=dataUrl.indexOf(',');
    const r=await api('/api/transcribe',{mime,name:'dictation-'+Date.now(),data:dataUrl.slice(comma+1)},70000);
    const result=await r.json();
    if(!r.ok)throw new Error(result.transcription_error||'语音提交失败，重试');
    const transcript=(result.transcript||'').trim();
    if(!transcript)throw new Error(result.transcription_error||'没识别出文字，重试');
    renderSpeechText(transcript);speechSession=null;
    setSpeechStatus('idle','识别完成，可编辑发送');
    box.focus();
  }catch(error){
    speechSession=null;setSpeechStatus('error',error.message||'转写失败，重试');
  }
}
async function startSpeech(){
  if(speechRecorder){setSpeechStatus('processing','录音结束，转写中…');speechRecorder.stop();return}
  if(speechPermissionPending||voiceRecorder||voicePermissionPending||voiceSending||busy||sending||stopping||stopped)return;
  speechCancelled=false;speechPermissionPending=true;speechSession={base:box.value};
  speechDraftActive=true;saveSpeechDraft(box.value);setSpeechStatus('processing','启动麦克风中…');
  try{
    const stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:{ideal:1},sampleRate:{ideal:16000}}});speechPermissionPending=false;
    if(speechCancelled){stream.getTracks().forEach(track=>track.stop());return}
    speechStream=stream;speechChunks=[];
    const mime=speechMime();speechRecorder=new MediaRecorder(stream,{audioBitsPerSecond:48000,...(mime?{mimeType:mime}:{})});
    speechRecorder.ondataavailable=event=>{if(event.data?.size)speechChunks.push(event.data)};
    speechRecorder.onstop=finishSpeech;speechRecorder.start(500);
    speechTimer=setTimeout(()=>stopSpeech(false),120000);
    setSpeechStatus('listening','听写中，再点结束转文字');
  }catch(error){
    speechPermissionPending=false;speechSession=null;stopSpeechTracks();
    if(speechCancelled){speechCancelled=false;setSpeechStatus('idle','已取消录音。');return}
    setSpeechStatus('error','无法使用麦克风，请在 Windows 允许');
  }
}
speechButton.onclick=()=>speechRecorder?stopSpeech(false):speechPermissionPending?stopSpeech(true):startSpeech();
window.addEventListener('pagehide',()=>{
  if(speechSession){saveSpeechDraft(box.value);stopSpeech(true);stopSpeechTracks()}
});
window.addEventListener('pagehide',()=>{
  if(speechSession)saveSpeechDraft(box.value);
});
function bubbleDelay(text){const n=Array.from(text).length;return n<=60?1200:n<=200?2400:3600}
function scrollIfFollowing(action){
  const stick=log.scrollHeight-log.scrollTop-log.clientHeight<80;
  action();if(stick)log.scrollTop=log.scrollHeight;
}
function showEvent(e){
  if(e.kind!=='error'&&e.id)displayedEventIds.add(e.id);
  if(e.kind==='message_removed'){
    removeMessageBubble(e.message_id);
    return;
  }
  if(e.kind==='message_recalled'){
    const node=findMessageBubble(e.message_id);
    if(node){closeMessageActions();node.classList.add('recalled');node.replaceChildren();node.textContent='已撤回这条消息';node.dataset.messageText='[Message recalled]';node.removeAttribute('data-media-id')}
    return;
  }
  if(e.kind==='message_updated'){
    const node=findMessageBubble(e.message_id);
    if(node){
      if(e.edited===true&&typeof e.text==='string'){node.dataset.messageText=e.text;node.replaceChildren();node.classList.remove('sticker-msg');if(!stickerBubble(node,e.text))node.innerHTML=render(e.text)}
      if(e.virtual_card){if(typeof e.text==='string'&&e.text)node.dataset.messageText=e.text;renderVirtualCard(node,e.virtual_card)}
      if(typeof e.favorite==='boolean')updateFavoriteBadge(node,e.favorite);
    }
    return;
  }
  if(e.kind==='voice_status'&&e.media?.id){
    const node=[...col.querySelectorAll('.msg')].find(item=>item.dataset.mediaId===e.media.id);
    if(node)updateVoiceMessage(node,e.media);
    return;
  }
  if(e.kind==='reset'){
    closeMessageActions();
    multiSelect=false;selectedMessageIds.clear();updateSelectionBar();
    col.replaceChildren();typing=null;previousMessage=null;roundStartedAt=0;roundHasReply=false;
    scrollIfFollowing(()=>add('notice',e.text,e.id,e.time));
    return;
  }
  if(e.kind==='user'&&e.media?.kind==='voice'){
    const pending=[...col.querySelectorAll('.msg')].find(node=>node.dataset.mediaId===e.media.id);
    if(pending)pending.remove();
  }
  if(e.kind==='trace'){scrollIfFollowing(()=>addTrace(e));return}
  if(e.kind==='notice'&&typeof e.text==='string'){
    // 工单 392：后端每轮都发同一句（QQ 通道找不到窗口那类），这里只显示第一条。
    // 只拦来自事件的提示；本地点按钮产生的回执（取消录音等）仍走 add()，不受影响。
    const key=e.calendar_undo_id||e.text.trim(),seenAt=recentNotices.get(key);
    if(seenAt!==undefined&&Date.now()-seenAt<NOTICE_REPEAT_MS)return;
    if(recentNotices.size>=200)recentNotices.clear();
    recentNotices.set(key,Date.now());
  }
  if(e.kind==='notice'&&typeof e.life_id==='string'){
    // Ticket 385: "Peter 改了你的…" opens that record; the line itself never enters Peter's history.
    scrollIfFollowing(()=>{const line=add('notice',e.text,e.id,e.time);if(!line)return;line.classList.add('life-notice');
      if(e.calendar_undo_id){const open=document.createElement('button');open.textContent='打开';open.onclick=()=>openLifeRecord(e.life_id);
        const undo=document.createElement('button');undo.textContent='撤销';undo.onclick=async()=>{undo.disabled=true;try{const r=await api('/api/life',{undo_auto:e.calendar_undo_id});const d=await r.json();if(!r.ok)throw Error(d.error||'撤销失败');undo.textContent='已撤销';await loadLife()}catch(err){undo.disabled=false;notify(err.message,'error')}};line.append(open,undo);
        if(e.calendar_can_remind){const choice=document.createElement('select');for(const [minutes,label] of [[0,'准点'],[15,'提前 15 分钟'],[30,'提前 30 分钟']]){const option=document.createElement('option');option.value=String(minutes);option.textContent=label;choice.append(option)}const remind=document.createElement('button');remind.textContent='提醒我';remind.onclick=async()=>{remind.disabled=true;try{const r=await api('/api/life',{id:e.life_id,remind:true,remind_before:Number(choice.value)});const d=await r.json();if(!r.ok)throw Error(d.error||'设置失败');remind.textContent='已提醒 · 改';remind.disabled=false;await loadLife()}catch(err){remind.disabled=false;notify(err.message,'error')}};line.append(choice,remind)}}
      else{line.setAttribute('role','button');line.tabIndex=0;line.title='打开这条记录';line.onclick=()=>openLifeRecord(e.life_id);line.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();openLifeRecord(e.life_id)}}}});
    return;
  }
  scrollIfFollowing(()=>add(e.kind,e.text,e.id,e.time,e.media,e.message_id,e.request_id,e.favorite,e.attachments,e.virtual_card,e.virtual_card_event));
  if(e.kind==='approval'&&e.id!==pendingApproval){
    const card=col.querySelector('.approval[data-id="'+e.id+'"]');
    card.classList.add('done');card.querySelector('.status').textContent='审批已结束';
  }
}
function revealNext(){
  if(revealTimer!==null||!revealQueue.length)return;
  const e=revealQueue[0];
  let delay=0;
  if(e.kind==='assistant')delay=Math.max(0,bubbleDelay(e.text)-(roundHasReply?0:Math.max(0,Date.now()-roundStartedAt)));
  const reveal=()=>{
    revealTimer=null;revealQueue.shift();
    if(e.kind==='user'){roundStartedAt=Date.parse(e.time)||Date.now();roundHasReply=false}
    if(e.kind==='assistant')roundHasReply=true;
    try{showEvent(e)}catch(err){
      console.error('event render failed',e&&e.id,err);
      try{add('error','有一条消息显示不了（编号 '+(e&&e.id)+'），其余消息照常')}catch(_){}
    }
    setBusy(busy);revealNext();
  };
  if(delay)revealTimer=setTimeout(reveal,delay);else reveal();
}
function receiveEvent(e,initial){
  if(e.id&&displayedEventIds.has(e.id))return;
  if(e.kind==='error'&&e.request_id&&displayedErrorRequests.has(e.request_id))return;
  const confirmedImage=e.kind==='user'&&e.request_id?awaitingImages.get(e.request_id):null;
  if(confirmedImage){if(selectedImage===confirmedImage)clearImage();awaitingImages.delete(e.request_id)}
  if(initial||e.history){
    if(e.kind==='user'){roundStartedAt=Date.parse(e.time)||Date.now();roundHasReply=false}
    if(e.kind==='assistant')roundHasReply=true;
    showEvent(e);
  }else{revealQueue.push(e);revealNext()}
}

function esc(s){return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function render(text){
  const match=text.match(/^> ([^\n]{1,100})：\n((?:>[^\n]*(?:\n|$))+)(?:\n)?([\s\S]*)$/);
  if(match){const preview=match[2].split('\n').filter(line=>line.startsWith('>')).map(line=>line.slice(1).replace(/^ /,'')).join('\n');
    return '<span class="chat-quote-preview"><strong>'+esc(match[1])+'</strong><span>'+esc(preview)+'</span></span>'+render(match[3]);}
  // Escape first, then allow only code blocks, inline code and bold.
  const parts=esc(text).split(/```/);
  return parts.map((p,i)=>i%2?'<pre><code>'+p.replace(/^[^\n]*\n/,'').replace(/\n$/,'')+'</code></pre>'
    :p.replace(i>0?/^\n/:/^$/,'').replace(i<parts.length-1?/\n$/:/^$/,'').replace(/`([^`\n]+)`/g,'<code>$1</code>').replace(/\*\*([^*\n]+)\*\*/g,'<b>$1</b>')).join('');
}
function formatVoiceTime(seconds){if(!Number.isFinite(seconds)||seconds<0)return'0:00';const n=Math.floor(seconds);return Math.floor(n/60)+':'+String(n%60).padStart(2,'0')}
function updateVoiceMessage(node,media){
  const wrap=node.querySelector('.msg-voice');if(!wrap)return;
  const transcript=(media.transcript||'').trim();
  let textBox=wrap.querySelector('.voice-transcript');
  if(!textBox){textBox=document.createElement('div');textBox.className='voice-transcript';textBox.hidden=true;wrap.appendChild(textBox)}
  textBox.textContent=transcript;
  let status=wrap.querySelector('.voice-status');
  if(!status){status=document.createElement('span');status.className='voice-status';wrap.appendChild(status)}
  const error=media.delivery_error||media.transcription_error;
  status.textContent=error||((media.transcription_pending||!transcript)?'正在转写…':'');
  status.classList.toggle('voice-error',!!error);
  let retry=wrap.querySelector('.voice-retry');
  if(error){
    if(!retry){retry=document.createElement('button');retry.type='button';retry.className='voice-retry';wrap.appendChild(retry)}
    retry.disabled=false;retry.textContent=media.delivery_error?'重试发送':'重试识别';
    retry.onclick=()=>retryVoice(media,retry,wrap);
  }else if(retry)retry.remove();
}
function createVoiceBubble(kind,text,media){
  const wrap=document.createElement('div');wrap.className='msg-voice';
  const row=document.createElement('div');row.className='voice-player';
  const play=document.createElement('button');play.type='button';play.className='voice-play';play.innerHTML=window.veilIcon('play',16);play.setAttribute('aria-label','播放语音');
  const wave=document.createElement('span');wave.className='voice-wave';wave.setAttribute('aria-hidden','true');
  for(let i=0;i<9;i++){const bar=document.createElement('i');wave.appendChild(bar)}
  const clock=document.createElement('span');clock.className='voice-clock';clock.textContent=formatVoiceTime(media.duration||0);
  const status=document.createElement('span');status.className='voice-status';
  const audio=document.createElement('audio');audio.preload='metadata';audio.hidden=true;
  audio.setAttribute('aria-label',kind==='assistant'?'Peter 的语音消息':'语音消息');
  if(media.localUrl)audio.src=media.localUrl;else if(canLoadChatMedia)window.veilDesktop.mediaUrl(media.filename).then(url=>{audio.src=url}).catch(()=>{});
  const updateDuration=()=>{if(Number.isFinite(audio.duration)&&audio.duration>0)clock.textContent=formatVoiceTime(audio.duration)};
  audio.onloadedmetadata=updateDuration;audio.ondurationchange=updateDuration;
  audio.onended=()=>{play.innerHTML=window.veilIcon('play',16);play.setAttribute('aria-label','播放语音');wrap.classList.remove('playing')};
  play.onclick=async()=>{if(audio.paused){try{await audio.play();play.innerHTML=window.veilIcon('pause',16);play.setAttribute('aria-label','暂停语音');wrap.classList.add('playing')}catch{status.textContent='这段语音播不了'}}else{audio.pause();play.innerHTML=window.veilIcon('play',16);play.setAttribute('aria-label','播放语音');wrap.classList.remove('playing')}};
  row.append(play,wave,clock);wrap.append(row,audio,status);
  if(kind==='assistant'){
    const line=document.createElement('div');line.className='voice-transcript';line.hidden=true;line.textContent=(text||'').trim();wrap.append(line);
  }else updateVoiceMessage({querySelector:()=>wrap},media);
  return wrap;
}
function formatAttachmentSize(size){
  if(!Number.isFinite(size)||size<0)return '';
  if(size<1024)return `${size} B`;
  if(size<1024*1024)return `${(size/1024).toFixed(1)} KB`;
  return `${(size/(1024*1024)).toFixed(1)} MB`;
}
function createAttachmentCard(attachment){
  if(!attachment||typeof attachment.id!=='string'||!/^[0-9a-f]{32}$/.test(attachment.id))return null;
  const card=document.createElement('section');card.className='msg-file-card';
  const heading=document.createElement('div');heading.className='msg-file-heading';
  const name=document.createElement('span');name.className='msg-file-name';name.textContent=attachment.name||'附件';
  const size=document.createElement('span');size.className='msg-file-size';size.textContent=formatAttachmentSize(attachment.size);
  heading.append(name,size);card.appendChild(heading);
  if(attachment.view==='image'&&['image/png','image/jpeg','image/gif','image/webp'].includes(attachment.mime)){
    const image=document.createElement('img');image.className='msg-file-image';image.alt=attachment.name||'图片';image.loading='lazy';
    if(canLoadChatMedia)window.veilDesktop.mediaUrl(attachment.filename).then(url=>{image.src=url}).catch(()=>{image.alt='图片附件读不了'});
    card.appendChild(image);
  }else if(attachment.view==='html'&&attachment.mime==='text/html'){
    const open=document.createElement('button');open.type='button';open.className='msg-file-open';open.textContent='在浏览器中打开';
    open.onclick=async()=>{open.disabled=true;try{await window.veilDesktop.openAttachment(attachment.id)}catch(error){notify(error.message||'打不开这个网页附件','error')}finally{open.disabled=false}};
    card.appendChild(open);
  }else if(attachment.view==='text'&&attachment.mime==='text/plain'){
    const details=document.createElement('details');details.className='msg-file-text';
    const summary=document.createElement('summary');summary.textContent='在聊天里查看';
    const content=document.createElement('pre');details.append(summary,content);
    let loaded=false,loading=false;
    details.addEventListener('toggle',async()=>{
      if(!details.open||loaded||loading)return;
      loading=true;content.textContent='正在读取…';
      try{content.textContent=await window.veilDesktop.attachmentText(attachment.id);loaded=true}
      catch(error){content.textContent=error.message||'读不了这个文本附件'}
      finally{loading=false}
    });
    card.appendChild(details);
  }else{
    const unsupported=document.createElement('span');unsupported.className='msg-file-note';unsupported.textContent='这个文件不能预览';card.appendChild(unsupported);
  }
  return card;
}
function updateFavoriteBadge(node,favorite){
  node.dataset.favorite=String(favorite);
  node.classList.toggle('is-favorite',favorite);
  let badge=node.querySelector('.msg-favorite');
  if(favorite&&!badge){badge=document.createElement('span');badge.className='msg-favorite';badge.innerHTML=window.veilIcon('star',13);badge.title='Favorite';badge.setAttribute('aria-label','Favorite');node.appendChild(badge)}
  else if(!favorite&&badge)badge.remove();
}
function characterName(){return (($('title')&&$('title').textContent)||'Peter').trim()||'Peter'}
function cardRelation(card){
  const name=characterName();
  if(card.type==='transfer')return card.sender==='user'?`转给 ${name}`:`${name} 转来`;
  return card.sender==='user'?`送给 ${name}`:`${name} 送来`;
}
function cardStatusText(card){
  const transfer={pending:'待接收',received:'已接收',declined:'已拒绝',returned:'已退回'};
  const gift={pending:'待收下',accepted:'已收下',declined:'已退回'};
  return (card.type==='transfer'?transfer:gift)[card.status]||'虚拟';
}
function virtualCardActions(card){
  if(card.recipient!=='user')return [];
  if(card.type==='transfer'&&card.status==='pending')return [['收下','card_receive'],['拒绝','card_decline']];
  if(card.type==='transfer'&&card.status==='received')return [['退回','card_return']];
  if(card.type==='gift'&&card.status==='pending')return [['收下','card_accept'],['婉拒','card_decline']];
  return [];
}
async function submitVirtualCardAction(button,action,messageId){
  const buttons=[...button.parentElement.querySelectorAll('button')];
  buttons.forEach(item=>item.disabled=true);
  try{
    const r=await api('/api/message/action',{action,message_id:messageId});
    const data=await r.json();
    if(!r.ok||!data||data.ok===false){
      buttons.forEach(item=>item.disabled=false);
      add('error',(data&&data.message)||'虚拟卡片操作失败，请重试。');
    }
  }catch(error){
    buttons.forEach(item=>item.disabled=false);
    add('error',error.message||'虚拟卡片操作失败，请重试。');
  }
}
function renderVirtualCard(node,card){
  if(!card||!['transfer','gift'].includes(card.type))return;
  node.classList.add('virtual-card-message');
  const cardNode=document.createElement('section');cardNode.className='virtual-card';
  const head=document.createElement('div');head.className='virtual-card-head';
  const icon=document.createElement('span');icon.className='virtual-card-icon';
  if(card.type==='gift')icon.textContent=card.icon||'🎁';
  else icon.innerHTML=window.veilIcon('wallet',18);
  const title=document.createElement('span');title.textContent=card.type==='transfer'?'虚拟转账':'虚拟礼物';head.append(icon,title);
  const value=document.createElement('strong');value.className='virtual-card-value';
  value.textContent=card.type==='transfer'?`¥${card.amount||'0'}`:(card.icon||'🎁');
  const relation=document.createElement('p');relation.className='virtual-card-note';relation.textContent=cardRelation(card);
  const note=document.createElement('p');note.className='virtual-card-note';
  note.textContent=card.type==='transfer'?(card.note||'仅聊天虚拟互动'):(card.content||'');
  const state=document.createElement('div');state.className='virtual-card-state';state.textContent=cardStatusText(card);
  cardNode.append(head,value,relation,note,state);
  const choices=virtualCardActions(card);
  if(choices.length){
    const actions=document.createElement('div');actions.className='virtual-card-actions';
    for(const [label,action] of choices){
      const button=document.createElement('button');button.type='button';button.textContent=label;
      button.onclick=()=>submitVirtualCardAction(button,action,node.dataset.messageId);
      actions.append(button);
    }
    cardNode.append(actions);
  }
  const favorite=node.dataset.favorite==='true';
  node.replaceChildren(cardNode);
  updateFavoriteBadge(node,favorite);
}
function renderVirtualCardEvent(node,event){
  if(!event||!['transfer','gift'].includes(event.type))return;
  node.classList.add('virtual-card-message');
  const cardNode=document.createElement('section');cardNode.className='virtual-card virtual-card-event';
  const head=document.createElement('div');head.className='virtual-card-head';
  const icon=document.createElement('span');icon.className='virtual-card-icon';
  if(event.type==='gift')icon.textContent=event.icon||'🎁';
  else icon.innerHTML=window.veilIcon('wallet',18);
  const title=document.createElement('span');title.textContent=event.type==='transfer'?'转账结果':'礼物结果';head.append(icon,title);
  const who=event.actor==='user'?'你':characterName();
  const verb=event.type==='transfer'?{receive:'收下了',decline:'拒绝了',return:'退回了'}[event.action]||'处理了':(event.action==='accept'?'收下了':'退回了');
  const value=document.createElement('strong');value.className='virtual-card-value';
  value.textContent=event.type==='transfer'?`${who}${verb} ¥${event.amount||''}`:`${who}${verb} ${event.icon||'🎁'}`;
  const note=document.createElement('p');note.className='virtual-card-note';
  note.textContent=event.type==='transfer'?(event.note||'仅聊天虚拟互动'):(event.content||'');
  const state=document.createElement('div');state.className='virtual-card-state';state.textContent='仅改这张虚拟卡片';
  cardNode.append(head,value,note,state);
  const favorite=node.dataset.favorite==='true';
  node.replaceChildren(cardNode);
  updateFavoriteBadge(node,favorite);
}
function add(kind,text,id,time,media,messageId,requestId,favorite=false,attachments=[],virtualCard=null,virtualCardEvent=null){
  if(kind==='error'){
    if(requestId&&displayedErrorRequests.has(requestId))return;
    if(id&&displayedEventIds.has(id))return;
    const previous=recentErrors.get(text),now=Date.now();
    if(previous&&now-previous.at<10000)return;
    if(requestId)displayedErrorRequests.add(requestId);
    if(id)displayedEventIds.add(id);
    recentErrors.set(text,{at:now});
  }
  const d=document.createElement('div');
  if(kind==='user'||kind==='assistant'){
    const stamp=Date.parse(time)||Date.now();
    if(!previousMessage||stamp-previousMessage.time>=GROUP_GAP){
      const label=document.createElement('div');label.className='timestamp';
      label.textContent=new Date(stamp).toLocaleString();
      if(typing)col.insertBefore(label,typing);else col.appendChild(label);
    }
    const grouped=previousMessage&&previousMessage.kind===kind&&Math.abs(stamp-previousMessage.time)<GROUP_GAP;
    d.className='msg '+kind+(grouped?' grouped':'');
    d.dataset.messageText=text;
    d.dataset.time=time||'';
    d.dataset.favorite=String(favorite===true);
    if(messageId){d.dataset.messageId=messageId;d.tabIndex=-1;d.addEventListener('contextmenu',event=>showMessageActions(event,d))}
    if(requestId)d.dataset.requestId=requestId;
    if(media?.filename)d.dataset.mediaFilename=media.filename;
    if(media&&media.kind==='voice'){
      d.dataset.mediaId=media.id;
      d.appendChild(createVoiceBubble(kind,text,media));
    }else if(media&&media.id&&media.mime?.startsWith('audio/')){
      d.appendChild(createVoiceBubble(kind,text,media));
    }else if(media&&media.id){
      const image=document.createElement('img');image.className='msg-image';if(canLoadChatMedia)window.veilDesktop.mediaUrl(media.filename).then(url=>{image.src=url}).catch(()=>{});image.alt=media.name||'图片';image.loading='lazy';d.appendChild(image);
      if(text&&text!=='[图片]'){const caption=document.createElement('div');caption.innerHTML=render(text);d.appendChild(caption)}
    }else if(virtualCard)renderVirtualCard(d,virtualCard);
    else if(virtualCardEvent)renderVirtualCardEvent(d,virtualCardEvent);
    else if(!stickerBubble(d,text))d.innerHTML=render(text);
    for(const attachment of Array.isArray(attachments)?attachments:[]){const card=createAttachmentCard(attachment);if(card)d.appendChild(card)}
    updateFavoriteBadge(d,d.dataset.favorite==='true');
    d.classList.toggle('is-selected',selectedMessageIds.has(messageId));
    previousMessage={kind,time:stamp};
  }
  else if(kind==='approval'){
    d.className='approval';d.dataset.id=id;
    const p=document.createElement('p');p.textContent=text;d.appendChild(p);
    const status=document.createElement('div');status.className='status';status.setAttribute('aria-live','polite');d.appendChild(status);
    for(const [label,ans,cls,iconName] of [['同意','y','yes','check'],['拒绝','n','','x']]){
      const b=document.createElement('button');b.type='button';b.dataset.answer=ans;b.innerHTML=window.veilIcon(iconName,14)+' '+label;b.className=cls;d.appendChild(b);
    }
  } else {d.className=kind==='error'?'error':'notice';d.textContent=text}
  if(typing)col.insertBefore(d,typing);else col.appendChild(d);
  if(kind==='error')recentErrors.set(text,{at:Date.now(),count:1,node:d});
  return d;
}
// Ticket 226: tool calls and the model's reasoning text sit in one folded block above the reply.
function traceBlock(requestId){
  // Approval cards and notices may sit between two steps of one reply; stop at a real chat bubble.
  for(let node=col.lastElementChild;node;node=node.previousElementSibling){
    if(node.classList.contains('think-block'))return (node.dataset.requestId||'')===(requestId||'')?node:null;
    if(node.classList.contains('msg'))return null;
  }
  return null;
}
function traceTitle(block){
  const thought=Number(block.dataset.thoughts)>0,tools=Number(block.dataset.tools)||0,sec=Math.round(Number(block.dataset.elapsed)||0);
  if(thought)return '思考了 '+Math.max(1,sec)+' 秒';
  return tools?'用了 '+tools+' 个工具':'思考中';
}
function addTrace(e){
  let block=traceBlock(e.request_id);
  if(!block){
    block=document.createElement('details');block.className='think-block';block.dataset.requestId=e.request_id||'';
    block.dataset.tools='0';block.dataset.thoughts='0';block.dataset.elapsed='0';
    const summary=document.createElement('summary');const body=document.createElement('div');body.className='think-body';
    block.append(summary,body);
    if(typing)col.insertBefore(block,typing);else col.appendChild(block);
  }
  const item=document.createElement('div');
  const isThought=e.trace==='thought';
  item.className='think-item '+(isThought?'think-thought':'think-tool');
  item.textContent=isThought?e.text:String(e.text).replace(/^🔧\s*/,'');
  block.querySelector('.think-body').append(item);
  const key=isThought?'thoughts':'tools';block.dataset[key]=String(Number(block.dataset[key])+1);
  if(Number.isFinite(e.elapsed))block.dataset.elapsed=String(Math.max(Number(block.dataset.elapsed)||0,e.elapsed));
  block.querySelector('summary').textContent=traceTitle(block);
}
let messageActionLayer=null;
let selectedMessageIds=new Set();
let multiSelect=false;
function findMessageBubble(messageId){
  if(!messageId)return null;
  return [...col.querySelectorAll('.msg[data-message-id]')].find(node=>node.dataset.messageId===messageId)||null;
}
function removeMessageBubble(messageId){
  const node=findMessageBubble(messageId);
  if(!node)return;
  selectedMessageIds.delete(messageId);updateSelectionBar();
  if(messageActionLayer?.dataset.messageId===messageId)closeMessageActions();
  node.remove();
  const last=[...col.querySelectorAll('.msg')].at(-1);
  previousMessage=last?{kind:last.classList.contains('user')?'user':'assistant',time:Date.parse(last.dataset.time)||Date.now()}:null;
}
function closeMessageActions(){
  if(!messageActionLayer)return;
  if(messageActionLayer.positionMenu){
    window.removeEventListener('resize',messageActionLayer.positionMenu);
    window.removeEventListener('scroll',messageActionLayer.positionMenu,true);
    window.visualViewport?.removeEventListener('resize',messageActionLayer.positionMenu);
    window.visualViewport?.removeEventListener('scroll',messageActionLayer.positionMenu);
  }
  messageActionLayer.remove();messageActionLayer=null;
  document.removeEventListener('keydown',dismissMessageActions);
}
function dismissMessageActions(event){if(event.key==='Escape')closeMessageActions()}
function canRegenerateMessage(node){
  const requestId=node.dataset.requestId;
  if(!requestId)return false;
  const messages=[...col.querySelectorAll('.msg[data-message-id]')],index=messages.indexOf(node);
  return index>=0&&!messages.slice(index+1).some(next=>next.classList.contains('user')||
    (next.classList.contains('assistant')&&next.dataset.requestId!==requestId));
}
function recallAllowed(node){
  const sentAt=Date.parse(node.dataset.time||'');
  const alreadyAnswered=[...col.querySelectorAll('.msg.assistant[data-message-id]')].some(reply=>reply.dataset.requestId===node.dataset.requestId);
  return node.classList.contains('user')&&!alreadyAnswered&&Number.isFinite(sentAt)&&Date.now()>=sentAt&&Date.now()-sentAt<=120000;
}
function quoteMessage(node){
  const who=node.classList.contains('assistant')?($('title').textContent||'Peter'):'我';
  pendingQuote={who,text:node.dataset.messageText||''};
  $('quote-author').textContent=who;$('quote-text').textContent=pendingQuote.text;$('quote-bar').hidden=false;
  box.focus();box.setSelectionRange(box.value.length,box.value.length);
}
function clearQuote() {pendingQuote=null;$('quote-bar').hidden=true;}
$('quote-cancel').onclick=clearQuote;
function memoryDisplay(raw){
  const match=raw.match(/^---\s*\r?\n([\s\S]*?)\r?\n---\s*(?:\r?\n|$)([\s\S]*)$/);
  if(!match)return {date:'',tags:[],body:raw.trim()};
  const lines=match[1].split(/\r?\n/),tags=[];let date='',inTags=false;
  for(const line of lines){
    if(/^created:\s*/.test(line))date=line.replace(/^created:\s*/, '').replace(/^['"]|['"]$/g,'');
    if(/^tags:\s*$/.test(line)){inTags=true;continue}
    if(inTags&&/^-\s+/.test(line)){tags.push(line.replace(/^-\s+/, '').replace(/^['"]|['"]$/g,''));continue}
    if(inTags&&!/^\s/.test(line))inTags=false;
  }
  const parsed=Date.parse(date);if(Number.isFinite(parsed))date=new Date(parsed).toLocaleString();
  return {date,tags:tags.slice(0,3),body:match[2].trim()};
}
async function showMessageMemory(node){
  const layer=document.createElement('div');layer.className='chat-confirm-layer';
  const backdrop=document.createElement('button');backdrop.type='button';backdrop.className='chat-confirm-backdrop';backdrop.setAttribute('aria-label','Close memory details');
  const dialog=document.createElement('section');dialog.className='chat-confirm-dialog message-memory-dialog';dialog.setAttribute('role','dialog');dialog.setAttribute('aria-modal','true');
  const title=document.createElement('h2');title.textContent='Recall Memory';
  const explanation=document.createElement('p');explanation.textContent='这条回复关联的记忆 · 显示当前内容';
  const content=document.createElement('div');content.className='message-memory-content';
  const status=document.createElement('p');status.textContent='读取记忆中…';content.appendChild(status);
  const buttons=document.createElement('div'),closeButton=document.createElement('button');closeButton.type='button';closeButton.textContent='Close';
  const close=()=>{layer.remove();node.focus({preventScroll:true})};backdrop.onclick=closeButton.onclick=close;
  dialog.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();close()}});
  buttons.append(closeButton);dialog.append(title,explanation,content,buttons);layer.append(backdrop,dialog);document.body.appendChild(layer);closeButton.focus();
  try{
    const result=await api('/api/message/action',{action:'memory',message_id:node.dataset.messageId});
    const data=await result.json();if(!result.ok)throw new Error(data.message||'读不到记忆引用');
    if(!Array.isArray(data.items)||!data.items.length){status.textContent='没有自动带入记忆';return}
    content.replaceChildren();
    for(const memoryId of data.items.slice(0,5)){
      const section=document.createElement('section');section.className='message-memory-entry';
      const heading=document.createElement('h3');heading.textContent='Memory';
      const body=document.createElement('pre');body.textContent='正在读取…';section.append(heading,body);content.appendChild(section);
      try{
        let offset=0,version='',text='';
        do{
          const params=new URLSearchParams({id:memoryId,offset:String(offset)});if(version)params.set('version',version);
          const pageResponse=await api('/api/memory/read?'+params.toString());const page=await pageResponse.json();
          if(!pageResponse.ok)throw new Error(page.error||'这条记忆读不了');
          if(typeof page.text!=='string'||typeof page.version!=='string')throw new Error('记忆格式无法识别');
          version=page.version;text+=page.text;offset=page.next_offset;
        }while(offset!==null);
        const memory=memoryDisplay(text);const kind=data.kinds&&typeof data.kinds[memoryId]==='string'?data.kinds[memoryId]:'';heading.textContent=[kind,memory.date,...memory.tags].filter(Boolean).join(' · ')||'Memory';
        body.textContent=memory.body||'（空记忆）';
      }catch(error){body.textContent=error.message||'这条记忆读不了'}
    }
    if(data.items.length>5){const note=document.createElement('p');note.textContent=`仅显示前 5 条，共 ${data.items.length} 条。`;content.appendChild(note)}
  }catch(error){status.textContent=error.message||'读不到记忆引用'}
}
function showMessageEditDialog(node){
  const layer=document.createElement('div');layer.className='chat-confirm-layer';
  const backdrop=document.createElement('button');backdrop.type='button';backdrop.className='chat-confirm-backdrop';backdrop.setAttribute('aria-label','Cancel edit');
  const dialog=document.createElement('section');dialog.className='chat-confirm-dialog chat-edit-dialog';dialog.setAttribute('role','dialog');dialog.setAttribute('aria-modal','true');dialog.setAttribute('aria-labelledby','message-edit-title');
  const title=document.createElement('h2');title.id='message-edit-title';title.textContent='Edit message';
  const input=document.createElement('textarea');input.value=node.dataset.messageText||'';input.maxLength=12000;input.rows=5;input.setAttribute('aria-label','Message text');
  const buttons=document.createElement('div'),cancel=document.createElement('button'),save=document.createElement('button');
  cancel.type=save.type='button';cancel.textContent='Cancel';save.textContent='Save';
  const close=()=>{layer.remove();node.focus({preventScroll:true})};backdrop.onclick=cancel.onclick=close;
  save.onclick=()=>{const content=input.value.trim();if(!content){notify('Message cannot be empty.','error');input.focus();return}close();void runMessageAction('edit',node.dataset.messageId,{content})};
  dialog.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();close();return}if(event.key==='Tab'){const targets=[input,cancel,save],index=targets.indexOf(document.activeElement);if(event.shiftKey&&index===0){event.preventDefault();save.focus()}else if(!event.shiftKey&&index===2){event.preventDefault();input.focus()}}});
  buttons.append(cancel,save);dialog.append(title,input,buttons);layer.append(backdrop,dialog);document.body.appendChild(layer);input.focus();input.setSelectionRange(input.value.length,input.value.length);
}
{
  const favoritesButton=$('favorites-filter');
  if(favoritesButton)favoritesButton.onclick=()=>{
    const on=!col.classList.contains('only-favorites');
    col.classList.toggle('only-favorites',on);favoritesButton.setAttribute('aria-pressed',String(on));
    if(!on)log.scrollTop=log.scrollHeight;
  };
}
function showMessageActions(event,node){
  event.preventDefault();
  if(busy||sending||stopping||stopped||node.classList.contains('recalled')||!node.dataset.messageId)return;
  closeMessageActions();
  const voice=node.classList.contains('voice-message')||!!node.querySelector('.msg-voice');
  const transcript=node.querySelector('.msg-voice .voice-transcript');
  const user=node.classList.contains('user'),favorite=node.dataset.favorite==='true';
  const actions=voice?[
    ['transcribe',transcript&&!transcript.hidden?'Hide text':'Transcribe','file-text',!transcript?.textContent.trim()],
    ['favorite',favorite?'Unfavorite':'Favorite','star',false],['download','Download','download',!node.dataset.mediaFilename],
    ['request','This request','eye',false],['delete','Delete','trash',false],...(user?[['recall','Recall','undo',!recallAllowed(node)]]:[])
  ]:[
    ['copy','Copy','copy',!(node.dataset.messageText||'').trim()||!!node.dataset.mediaFilename],
    ['edit','Edit','pencil',node.classList.contains('virtual-card-message')||!((node.dataset.messageText||'').trim())||!!node.dataset.mediaFilename],
    ['quote','Quote','quote',!(node.dataset.messageText||'').trim()],
    ['memory','Memory','brain',user],['request','This request','eye',false],['select','Select','select',false],
    [user?'recall':'favorite',user?'Recall':(favorite?'Unfavorite':'Favorite'),user?'undo':'star',user&&!recallAllowed(node)],
    ['delete','Delete','trash',false],['regenerate','Regenerate','rotate-ccw',user||!canRegenerateMessage(node)]
  ];
  const layer=document.createElement('div');layer.className='chat-action-layer';layer.dataset.messageId=node.dataset.messageId;
  const backdrop=document.createElement('button');backdrop.type='button';backdrop.className='chat-action-backdrop';backdrop.setAttribute('aria-label','Close message actions');backdrop.onclick=closeMessageActions;
  const selected=node.cloneNode(true);selected.classList.remove('is-selected');selected.classList.add('chat-action-selected');selected.removeAttribute('data-message-id');selected.removeAttribute('tabindex');selected.setAttribute('aria-hidden','true');
  const menu=document.createElement('div');menu.className='chat-message-actions';menu.setAttribute('role','menu');menu.setAttribute('aria-label','Message actions');
  for(const [action,label,iconName,disabled] of actions){
    const button=document.createElement('button');button.type='button';button.className='chat-action-item'+(action==='delete'?' danger':'');button.setAttribute('role','menuitem');
    button.disabled=disabled===true;
    button.innerHTML=window.veilIcon(iconName,24)+`<small>${label}</small>`;
    button.onclick=()=>{
      if(action==='delete'){closeMessageActions();showMessageDeleteConfirm(node);return}
      if(action==='copy'){
        const content=node.dataset.messageText||'';closeMessageActions();
        (window.veilDesktop?.copyText?window.veilDesktop.copyText(content):navigator.clipboard.writeText(content)).then(()=>notify('消息已复制。')).catch(()=>notify('复制失败','error'));
        return;
      }
      if(action==='transcribe'){
        const text=node.querySelector('.msg-voice .voice-transcript');
        if(text)text.hidden=!text.hidden;
        closeMessageActions();return;
      }
      if(action==='quote'){closeMessageActions();quoteMessage(node);return}
      if(action==='memory'){closeMessageActions();showMessageMemory(node);return}
      if(action==='request'){closeMessageActions();openSnapshotForMessage(node.dataset.messageId);return}
      if(action==='select'){closeMessageActions();multiSelect=true;selectedMessageIds.add(node.dataset.messageId);updateSelectionBar();refreshMessageSelection();return}
      if(action==='download'){
        const filename=node.dataset.mediaFilename;closeMessageActions();
        if(filename)window.veilDesktop.mediaUrl(filename).then(url=>{const link=document.createElement('a');link.href=url;link.download=filename;link.click()}).catch(()=>notify('Unable to download this voice message.','error'));
        return;
      }
      if(action==='edit'){closeMessageActions();showMessageEditDialog(node);return}
      if(action==='favorite'){closeMessageActions();void runMessageAction('favorite',node.dataset.messageId,{favorite:node.dataset.favorite!=='true'});return}
      closeMessageActions();void runMessageAction(action,node.dataset.messageId);
    };
    menu.appendChild(button);
  }
  layer.append(backdrop,selected,menu);document.body.appendChild(layer);messageActionLayer=layer;
  const positionMenu=()=>{
    if(!node.isConnected){closeMessageActions();return}
    const area=$('log').getBoundingClientRect(),bubble=node.getBoundingClientRect(),width=menu.offsetWidth,height=menu.offsetHeight,margin=8,gap=9;
    selected.style.left=`${bubble.left}px`;selected.style.top=`${Math.max(area.top,bubble.top)}px`;selected.style.width=`${bubble.width}px`;selected.style.maxHeight=`${Math.max(0,Math.min(bubble.bottom,area.bottom)-Math.max(bubble.top,area.top))}px`;
    const left=Math.max(area.left+margin,Math.min((bubble.left+bubble.right-width)/2,area.right-margin-width));
    const below=area.bottom-margin-(bubble.bottom+gap),above=bubble.top-gap-area.top-margin;
    const placeBelow=height<=below||(height>above&&below>=above);
    const available=Math.max(0,area.bottom-area.top-2*margin);
    const top=placeBelow?bubble.bottom+gap:bubble.top-gap-Math.min(height,available);
    menu.style.maxHeight=`${available}px`;menu.style.overflowY='auto';
    menu.style.left=`${left}px`;menu.style.top=`${Math.max(area.top+margin,Math.min(top,area.bottom-margin-Math.min(height,available)))}px`;
  };
  layer.positionMenu=positionMenu;positionMenu();
  window.addEventListener('resize',positionMenu);window.addEventListener('scroll',positionMenu,true);
  window.visualViewport?.addEventListener('resize',positionMenu);window.visualViewport?.addEventListener('scroll',positionMenu);
  menu.querySelector('button')?.focus();document.addEventListener('keydown',dismissMessageActions);
}
function showMessageDeleteConfirm(node,messageIds=null){
  const layer=document.createElement('div');layer.className='chat-confirm-layer';
  const backdrop=document.createElement('button');backdrop.type='button';backdrop.className='chat-confirm-backdrop';backdrop.setAttribute('aria-label','Cancel delete');
  const dialog=document.createElement('section');dialog.className='chat-confirm-dialog';dialog.setAttribute('role','alertdialog');dialog.setAttribute('aria-modal','true');dialog.setAttribute('aria-labelledby','message-delete-title');
  const title=document.createElement('h2');title.id='message-delete-title';title.textContent=messageIds?.length>1?'Delete selected messages?':'Delete message?';
  const text=document.createElement('p');text.textContent='This will permanently remove the selected chat record(s), attached media when no longer referenced, and their future conversation context.';
  const buttons=document.createElement('div'),cancel=document.createElement('button'),remove=document.createElement('button');
  cancel.type=remove.type='button';cancel.textContent='Cancel';remove.textContent='Delete';remove.className='danger';
  const close=()=>{layer.remove();node?.focus({preventScroll:true})};backdrop.onclick=cancel.onclick=close;
  dialog.addEventListener('keydown',event=>{
    if(event.key==='Escape'){event.preventDefault();close();return}
    if(event.key==='Tab'){
      const targets=[cancel,remove],index=targets.indexOf(document.activeElement);
      if(event.shiftKey&&index===0){event.preventDefault();remove.focus()}
      else if(!event.shiftKey&&index===1){event.preventDefault();cancel.focus()}
    }
  });
  remove.onclick=()=>{close();void runMessageAction('delete',node?.dataset.messageId,messageIds?{message_ids:messageIds}:undefined)};
  buttons.append(cancel,remove);dialog.append(title,text,buttons);layer.append(backdrop,dialog);document.body.appendChild(layer);remove.focus();
}
async function runMessageAction(action,messageId,extra={}){
  try{
    const response=await api('/api/message/action',{action,message_id:messageId,...extra});
    const result=await response.json();
    if(!response.ok||!result.ok)throw new Error(result.message||'Message action failed.');
    notify(result.message||'Done');
    if(action==='delete'&&extra.message_ids){multiSelect=false;selectedMessageIds.clear();updateSelectionBar();refreshMessageSelection()}
    if(action==='regenerate')setBusy(true);
  }catch(error){notify(error.message||'Message action failed.','error')}
}
function refreshMessageSelection(){
  document.querySelectorAll('.msg[data-message-id]').forEach(node=>node.classList.toggle('is-selected',selectedMessageIds.has(node.dataset.messageId)));
}
function updateSelectionBar(){
  const bar=$('selection-bar');if(!bar)return;
  bar.hidden=!multiSelect;$('selection-count').textContent=`已选 ${selectedMessageIds.size} 条`;
  $('selection-delete').disabled=selectedMessageIds.size===0;
}
function setBusy(b){
  busy=b;const waiting=b||revealQueue.length>0;sendBtn.disabled=sending||voiceSending||stopping||stopped||speechIsActive()||!!voiceRecorder||voicePermissionPending;
  voiceHold.disabled=(waiting||sending||stopping||stopped||voiceSending||speechIsActive())&&!voiceRecorder;
  if(waiting&&!typing&&!stopped){typing=document.createElement('div');typing.id='typing';typing.innerHTML='<span></span><button type="button" class="stop-turn" title="让他说完这一步就停">停下</button>';typing.querySelector('button').addEventListener('click',stopTurn);col.appendChild(typing)}
  if(waiting&&typing){typing.firstChild.textContent=transcribingVoice?'转写中…':($('title').textContent||'')+' 正在回复…';const stopButton=typing.querySelector('button');stopButton.hidden=!b||transcribingVoice;if(!b)stopButton.disabled=false}
  if((!waiting||stopped)&&typing){typing.remove();typing=null}
}
// Ticket 138: Lan can stop a reply that keeps going; Peter stops before his next step.
async function stopTurn(e){
  const button=e.currentTarget;button.disabled=true;const label=button.textContent;button.textContent='正在停…';
  try{const r=await api('/api/stop',{});if(!r.ok){button.disabled=false;button.textContent=label}}catch(err){button.disabled=false;button.textContent=label}
}
async function api(path,body,timeoutMs=30000){
  const result=await window.veilDesktop.request(path,body,timeoutMs);
  return {ok:result.ok,status:result.status,json:async()=>result.data};
}
function memoryDay(created){
  const date=new Date(created);if(!created||Number.isNaN(date.getTime()))return '没有日期';
  const today=new Date();today.setHours(0,0,0,0);const day=new Date(date);day.setHours(0,0,0,0);
  const diff=Math.round((today-day)/86400000);
  return diff===0?'今天':diff===1?'昨天':`${date.getMonth()+1}月${date.getDate()}日`;
}
function memoryTime(created){
  const date=new Date(created);if(!created||Number.isNaN(date.getTime()))return '';
  return `${String(date.getHours()).padStart(2,'0')}:${String(date.getMinutes()).padStart(2,'0')}`;
}
function memoryMatches(item,query){
  if(!query)return true;
  const fields=[item.title,...item.domain,...item.tags,item.why,item.body];
  return fields.some(text=>String(text||'').toLocaleLowerCase().includes(query));
}
async function loadEvents(){
  const list=$('memory-list');list.textContent='读取经历中…';
  try{const r=await api('/api/memory/events');const d=await r.json();if(!r.ok)throw new Error(d.error||'读取失败');eventItems=d.items||[];renderEventList()}
  catch(error){list.textContent=error.message||'经历暂时读不了'}
}
function renderEventList(){
  const list=$('memory-list'),query=$('memory-search').value.trim().toLocaleLowerCase();list.replaceChildren();
  $('memory-heading').textContent='经历';
  const filtered=eventItems.filter(item=>!query||String(item.title||'').toLocaleLowerCase().includes(query));
  if(!filtered.length){const empty=document.createElement('p');empty.className='muted';empty.textContent=query?'没有匹配的经历。':'还没有形成经历';list.appendChild(empty)}
  let lastDay='';
  for(const item of filtered){
    const day=memoryDay(item.last_at);
    if(day!==lastDay){const heading=document.createElement('h4');heading.className='memory-day';heading.textContent=day;list.appendChild(heading);lastDay=day}
    const button=document.createElement('button');button.type='button';button.className='memory-entry';
    const top=document.createElement('span');top.className='memory-entry-top';
    const title=document.createElement('span');title.className='memory-entry-title';title.textContent=item.title||item.event_id;
    const time=document.createElement('time');time.textContent=(item.status==='uncertain'?'未定 · ':'')+`${item.stage_count||0} 段 · `+memoryTime(item.last_at);top.append(title,time);
    const preview=document.createElement('span');preview.className='memory-entry-preview';preview.textContent=`${memoryDay(item.first_at)} → ${memoryDay(item.last_at)}`;
    button.append(top,preview);button.onclick=()=>readEvent(item);list.appendChild(button);
  }
  $('memory-count').textContent=`共 ${eventItems.length} 段经历`;
}
async function readEvent(item){
  const detail=$('memory-detail');delete detail.dataset.memoryId;detail.textContent='读取经历中…';
  try{
    const r=await api('/api/memory/event?id='+encodeURIComponent(item.event_id));const d=await r.json();if(!r.ok)throw new Error(d.error||'读取失败');
    const eyebrow=document.createElement('p');eyebrow.className='eyebrow';eyebrow.textContent='EVENT STORY';
    const heading=document.createElement('h3');heading.textContent=d.title||d.event_id;
    const facts=document.createElement('p');facts.className='memory-facts';
    facts.textContent=[`${memoryDay(d.first_at)} → ${memoryDay(d.last_at)}`,d.status==='uncertain'?'未定':d.status,
      d.stages.length?`${d.stages.length} 段`:'',typeof d.confidence==='number'?`置信度 ${Math.round(d.confidence*100)}%`:''].filter(Boolean).join('　');
    const story=document.createElement('pre');story.style.whiteSpace='pre-wrap';story.style.fontFamily='inherit';story.textContent=d.story||'';
    const stages=document.createElement('details');stages.className='memory-raw';
    const summary=document.createElement('summary');summary.textContent=`阶段与证据（${d.stages.length}）`;stages.appendChild(summary);
    if(d.stages.length>1){
      const editor=document.createElement('div');editor.className='memory-status';editor.style.marginTop='8px';
      const pickEvent=document.createElement('select');pickEvent.setAttribute('aria-label','目标经历');
      const blank=document.createElement('option');blank.value='';blank.textContent='移到…';pickEvent.appendChild(blank);
      for(const other of eventItems){if(other.event_id!==d.event_id){const option=document.createElement('option');option.value=other.event_id;option.textContent=other.title;pickEvent.appendChild(option)}}
      const split=document.createElement('button');split.type='button';split.textContent='拆出所选';
      const move=document.createElement('button');move.type='button';move.textContent='移到所选经历';
      const status=document.createElement('span');status.className='muted';
      const chosen=()=>[...stages.querySelectorAll('input.event-stage-pick:checked')].map(box=>box.value);
      split.onclick=async()=>{const ids=chosen();if(ids.length<1||ids.length>=d.stages.length){status.textContent='勾选要拆出的阶段（不能全部）';return}
        try{const response=await api('/api/memory/event/split',{id:d.event_id,stage_ids:ids});if(!response.ok){const data=await response.json();throw new Error(data.error||'拆分失败')}status.textContent='已拆出';await loadEvents();await readEvent(item)}catch(error){status.textContent=error.message||'拆分失败'}};
      move.onclick=async()=>{const ids=chosen();if(ids.length!==1||!pickEvent.value){status.textContent='勾选一个阶段并选目标经历';return}
        try{const response=await api('/api/memory/event/move',{id:ids[0],to:pickEvent.value});if(!response.ok){const data=await response.json();throw new Error(data.error||'移动失败')}status.textContent='已移动';await loadEvents();await readEvent(item)}catch(error){status.textContent=error.message||'移动失败'}};
      editor.append(pickEvent,split,move,status);stages.appendChild(editor);
    }
    for(const stage of d.stages){
      const box=document.createElement('div');box.className='memory-status';box.style.marginTop='10px';
      const pick=document.createElement('input');pick.type='checkbox';pick.className='event-stage-pick';pick.value=stage.stage_id;box.appendChild(pick);
      const time=document.createElement('time');time.textContent=strTime(stage.at);box.appendChild(time);
      const text=document.createElement('p');text.textContent=stage.text;box.appendChild(text);
      if(stage.interpretation){const read=document.createElement('p');read.className='muted';read.textContent='他当时理解：'+stage.interpretation;box.appendChild(read)}
      for(const proof of stage.evidence||[]){const line=document.createElement('p');line.className='muted';line.textContent=`${proof.kind} · ${String(proof.text||'').slice(0,200)}`;box.appendChild(line)}
      stages.appendChild(box);
    }
    detail.replaceChildren(eyebrow,heading,facts,story);
    if((d.belief_history||[]).length){
      const beliefs=document.createElement('details');beliefs.className='memory-raw';
      const bsum=document.createElement('summary');bsum.textContent=`理解变化（${d.belief_history.length}）`;beliefs.appendChild(bsum);
      for(const belief of d.belief_history){
        const line=document.createElement('p');line.className='memory-status';
        line.textContent=`${strTime(belief.at)}　${belief.belief}`;beliefs.appendChild(line);
      }
      detail.appendChild(beliefs);
    }
    if((d.related||[]).length){
      const related=document.createElement('div');related.className='memory-status';
      const label=document.createElement('p');label.className='muted';label.textContent='相关经历';related.appendChild(label);
      for(const item of d.related){
        const line=document.createElement('p');line.textContent=`${item.title} · ${memoryDay(item.last_at)}`;related.appendChild(line);
      }
      detail.appendChild(related);
    }
    detail.appendChild(stages);
    if((d.versions||[]).length){
      const versions=document.createElement('details');versions.className='memory-raw';
      const vsum=document.createElement('summary');vsum.textContent=`历史版本（${d.versions.length}）`;versions.appendChild(vsum);
      for(const version of d.versions){
        const box=document.createElement('div');box.className='memory-status';box.style.marginTop='10px';
        const when=document.createElement('time');when.textContent=strTime(version.at);box.appendChild(when);
        const old=document.createElement('pre');old.style.whiteSpace='pre-wrap';old.style.fontFamily='inherit';
        old.textContent=version.old_story||version.old_content||'';box.appendChild(old);versions.appendChild(box);
      }
      detail.appendChild(versions);
    }
  }catch(error){detail.textContent=error.message||'这段经历读不了'}
}
function strTime(value){const date=new Date(value);return Number.isNaN(date.getTime())?'':`${date.getFullYear()}.${date.getMonth()+1}.${date.getDate()} ${String(date.getHours()).padStart(2,'0')}:${String(date.getMinutes()).padStart(2,'0')}`}
function renderMemoryList(){
  if(MEMORY_GROUPS[memoryGroup].events){renderEventList();return}
  if(MEMORY_GROUPS[memoryGroup].archived){renderArchivedList();return}
  const list=$('memory-list'),query=$('memory-search').value.trim().toLocaleLowerCase(),group=MEMORY_GROUPS[memoryGroup];list.replaceChildren();
  $('memory-heading').textContent=group.label;
  const filtered=memoryItems.filter(item=>!item.source_record&&(!group.domains||item.domain.some(name=>group.domains.includes(name)))&&memoryMatches(item,query));
  if(!filtered.length){const empty=document.createElement('p');empty.className='muted';empty.textContent=query?'没有匹配的记忆。':'还没有记忆';list.appendChild(empty)}
  let lastDay='';
  for(const item of filtered.slice(0,memoryShown)){
    const day=memoryDay(item.created);
    if(day!==lastDay){const heading=document.createElement('h4');heading.className='memory-day';heading.textContent=day;list.appendChild(heading);lastDay=day}
    const button=document.createElement('button');button.type='button';button.className='memory-entry';
    const top=document.createElement('span');top.className='memory-entry-top';
    const title=document.createElement('span');title.className='memory-entry-title';title.textContent=item.surprise?'🎁 '+item.title:item.title;
    const time=document.createElement('time');time.textContent=(item.locked?'🔒 ':'')+(item.peter_after_lan?'他改过你改的 · ':'')+(item.auto?'自动整理 · ':'')+memoryTime(item.created);top.append(title,time);
    const preview=document.createElement('span');preview.className='memory-entry-preview';
    preview.textContent=item.surprise?'惊喜，先不剧透':(item.body||item.why).replace(/\s+/g,' ').slice(0,90);
    button.append(top,preview);
    button.setAttribute('aria-current',String($('memory-detail').dataset.memoryId===item.memory_id));
    button.onclick=()=>readMemory(item);list.appendChild(button);
  }
  if(filtered.length>memoryShown){const more=document.createElement('button');more.type='button';more.className='memory-entry more';more.textContent=`看更早的（还有 ${filtered.length-memoryShown} 条）`;more.onclick=()=>{memoryShown+=50;renderMemoryList()};list.appendChild(more)}
  const total=memoryItems.filter(item=>!item.source_record).length;
  $('memory-count').textContent=group.domains||query?`显示 ${filtered.length} 条 · 共 ${total} 条记忆`:`共 ${total} 条记忆`;
}
// Ticket 363: memories folded into another one (or let fade by Ombre) are archived, not deleted; Lan can bring one back.
async function renderArchivedList(){
  const list=$('memory-list');$('memory-heading').textContent='已归档';list.textContent='读取归档记忆中…';
  try{const r=await api('/api/memory/archived',{id:''});const d=await r.json();if(!r.ok)throw new Error(d.error||'读取失败');
    list.replaceChildren();
    if(!d.items.length){const empty=document.createElement('p');empty.className='muted';empty.textContent='没有归档记忆';list.appendChild(empty)}
    for(const item of d.items){const box=document.createElement('div');box.className='memory-entry';
      const top=document.createElement('span');top.className='memory-entry-top';const title=document.createElement('span');title.className='memory-entry-title';title.textContent=item.title;
      const time=document.createElement('time');time.textContent=memoryTime(item.created);top.append(title,time);
      const preview=document.createElement('span');preview.className='memory-entry-preview';
      preview.textContent=(item.merged_into?'并进了「'+(item.merged_into_title||item.merged_into)+'」 · ':'')+item.body.replace(/\s+/g,' ').slice(0,140);
      const back=document.createElement('button');back.type='button';back.textContent='恢复';
      back.onclick=async()=>{back.disabled=true;try{const r2=await api('/api/memory/unarchive',{id:item.memory_id});const d2=await r2.json();if(!r2.ok)throw new Error(d2.error||'没能恢复。');notify(d2.message||'已恢复。');memoryItems=[];await loadMemoryCatalog(true)}
        catch(error){notify(error.message||'没能恢复。','error');back.disabled=false}};
      box.append(top,preview,back);list.appendChild(box)}
    $('memory-count').textContent=`已归档 ${d.items.length} 条`;
  }catch(error){list.textContent=error.message||'读取失败'}
}
// Ticket 354: the program writes down what Peter would remember of the chat; Lan can see the last result and switch it off.
let experienceEnabled=false;
function renderExperience(x){
  const row=$('experience-row');if(!x||x.available!==true){row.hidden=true;return}
  row.hidden=false;experienceEnabled=x.enabled===true;
  let last='还没有整理过';
  if(x.last){const at=new Date(x.last.time);const when=Number.isNaN(at.getTime())?'':`${at.getMonth()+1}月${at.getDate()}日 ${memoryTime(x.last.time)}`;
    if(x.last.kind==='budget')last=when+' 今天已到 ¥'+x.daily_budget_cny+' 上限，没再整理';
    else if(x.last.kind==='error'){
      // Ticket 386: say why in one line, and when it is tried again (30 minutes, 2 hours, then the next day).
      const detail=String(x.last.detail||'');
      const why=detail.includes('思考超字数')?(x.last.retried_without_thinking?'思考超字数，重试失败':'思考超字数，额度不够')
        :detail.includes('请求失败')?'模型请求失败':'模型没给出可用结果';
      let again='下次再试';
      if(x.retry_at){const r=new Date(x.retry_at);if(!Number.isNaN(r.getTime()))again=(r.getDate()!==new Date().getDate()?'明天':memoryTime(x.retry_at))+' 再试'}
      last=when+' 没整理成：'+why+'，'+again;}
    else if(x.last.written||x.last.updated)last=when+' 记了 '+(x.last.written||0)+' 条'+(x.last.updated?'，补充了 '+x.last.updated+' 条':'');
    else if(!x.last.unfixed)last=when+' 他觉得没什么可记';
    else last=when+' 看过了';
    if(x.last.kind==='checkpoint'&&x.last.retried_without_thinking)last+='（关掉思考后重试成功）';
    if(x.last.kind==='checkpoint'&&x.last.unfixed)last+='；有 '+x.last.unfixed+' 条草稿没存上，原文已留存';}
  $('experience-status').textContent=experienceEnabled
    ?'自动整理经历：开 · 上次：'+last+' · 攒了 '+x.pending_chars+' / '+x.min_chars+' 字 · 今天花了 ¥'+(x.spent_today||'?')+'（上限 ¥'+x.daily_budget_cny+'）'
    :'自动整理经历：关';
  $('experience-toggle').textContent=experienceEnabled?'关闭自动整理':'开启自动整理';
}
$('experience-toggle').onclick=async()=>{
  $('experience-toggle').disabled=true;
  try{const r=await api('/api/experience-memory',{enabled:!experienceEnabled});const d=await r.json();
    if(!r.ok)throw new Error(d.error||'');renderExperience(d.experience)}
  catch(e){notify('开关没存上，稍后再试','error')}
  finally{$('experience-toggle').disabled=false}
};
// Ticket 382: a small judge picks which memories fit what she just said; Lan can see its last result and switch it off.
let judgeEnabled=true;
function renderJudge(x){
  const row=$('judge-row');if(!x||x.available!==true){row.hidden=true;return}
  row.hidden=false;judgeEnabled=x.enabled===true;
  let last='还没有判断过';
  if(x.last){const at=new Date(x.last.time);const when=Number.isNaN(at.getTime())?'':memoryTime(x.last.time);
    if(x.last.kind==='failed')last=when+' 判官没成功（'+(x.last.reason||'')+'），这轮照旧';
    else if(x.last.kind==='empty')last=when+' 这轮没带旧事';
    else last=when+' 选了 '+(x.last.picked||0)+' 条';}
  $('judge-status').textContent=judgeEnabled
    ?'想起判官：开 · 上次：'+last+(x.breaker_open?' · 连续失败，暂停中':'')+' · 今天花了 ¥'+(x.spent_today||'?')
    :'想起判官：关';
  $('judge-toggle').textContent=judgeEnabled?'关闭想起判官':'开启想起判官';
}
$('judge-toggle').onclick=async()=>{
  $('judge-toggle').disabled=true;
  try{const r=await api('/api/recall-judge',{enabled:!judgeEnabled});const d=await r.json();
    if(!r.ok)throw new Error(d.error||'');renderJudge(d.recall_judge)}
  catch(e){notify('开关没存上，稍后再试','error')}
  finally{$('judge-toggle').disabled=false}
};
async function loadMemoryCatalog(reset=false){
  if(memoryLoading)return;
  memoryLoading=true;memoryCursor='';
  if(reset){memoryLoaded=false;memoryItems=[];$('memory-list').textContent='读取记忆中…'}
  try{
    const r=await api('/api/memory/panel');const data=await r.json();
    if(!r.ok)throw new Error(data.error||'记忆暂时读不了');
    if(!data||!Array.isArray(data.items))throw new Error('记忆格式无法识别');
    memoryItems=data.items.filter(item=>item&&typeof item.memory_id==='string'&&typeof item.title==='string')
      .map(item=>({...item,domain:Array.isArray(item.domain)?item.domain:[],tags:Array.isArray(item.tags)?item.tags:[]}));
    memoryLoaded=true;memoryAvailable=true;renderExperience(data.experience);renderJudge(data.recall_judge);renderMemoryList();
    const current=memoryItems.find(item=>item.memory_id===$('memory-detail').dataset.memoryId);if(current)readMemory(current);
  }catch(error){
    memoryLoaded=true;$('memory-list').textContent=error.message||'记忆暂时读不了';$('memory-unavailable').hidden=false;
    $('memory-count').textContent='读取失败';
  }finally{memoryLoading=false}
}
async function readRawMemory(memoryId){
  let offset=0,version='',content='';
  do{
    const params=new URLSearchParams({id:memoryId,offset:String(offset)});if(version)params.set('version',version);
    const r=await api('/api/memory/read?'+params.toString());const page=await r.json();
    if(!r.ok)throw new Error(page.error||'这条记忆读不了');
    if(typeof page.text!=='string'||typeof page.version!=='string')throw new Error('Ombre 记忆格式无法识别');
    version=page.version;content+=page.text;offset=page.next_offset;
  }while(offset!==null);
  return content;
}
function memoryField(label,value,multiline,rows=3){
  const wrap=document.createElement('label');wrap.className='memory-field';
  const name=document.createElement('span');name.textContent=label;
  const input=document.createElement(multiline?'textarea':'input');input.value=value;if(multiline)input.rows=rows;
  wrap.append(name,input);return {wrap,input};
}
function readMemory(item){
  const detail=$('memory-detail');detail.dataset.memoryId=item.memory_id;renderMemoryList();
  const eyebrow=document.createElement('p');eyebrow.className='eyebrow';eyebrow.textContent='MEMORY DETAIL';
  const heading=document.createElement('h3');heading.textContent=item.surprise?'🎁 '+item.title:item.title;
  const date=new Date(item.created);
  const facts=document.createElement('p');facts.className='memory-facts';
  facts.textContent=[Number.isNaN(date.getTime())?'':`记录于 ${date.getFullYear()}.${date.getMonth()+1}.${date.getDate()} ${memoryTime(item.created)}`,
    item.domain.length?'分类 · '+item.domain.join(' / '):'',item.importance?`重要度 ${item.importance}`:'',item.recalled?`最近想起过 ${item.recalled} 次`:''].filter(Boolean).join('　');
  if(item.surprise){
    const note=document.createElement('p');note.className='memory-surprise';note.textContent='惊喜内容不能编辑';
    detail.replaceChildren(eyebrow,heading,facts,note);return;
  }
  const title=memoryField('标题',item.title,false),why=memoryField('他为什么记这条',item.why,true,2),body=memoryField('记忆内容',item.body,true,8);
  const status=document.createElement('p');status.className='memory-status';status.setAttribute('aria-live','polite');
  const actions=document.createElement('div');actions.className='memory-actions';
  const save=document.createElement('button');save.type='button';save.className='primary';save.textContent='保存修改';
  const reset=document.createElement('button');reset.type='button';reset.textContent='还原';
  const remove=document.createElement('button');remove.type='button';remove.className='danger';remove.textContent='删除这条';
  actions.append(save,reset,remove);
  const original={title:item.title,why_remembered:item.why,content:item.body};
  const inputs={title:title.input,why_remembered:why.input,content:body.input};
  const changes=()=>Object.fromEntries(Object.entries(inputs).filter(([key,input])=>input.value!==original[key]).map(([key,input])=>[key,input.value]));
  const sync=()=>{const dirty=Object.keys(changes()).length>0;save.disabled=!dirty;reset.disabled=!dirty};
  Object.values(inputs).forEach(input=>input.addEventListener('input',sync));sync();
  reset.onclick=()=>{for(const [key,input] of Object.entries(inputs))input.value=original[key];status.textContent='';sync()};
  save.onclick=async()=>{
    save.disabled=true;status.textContent='正在保存…';
    try{const r=await api('/api/memory/edit',{id:item.memory_id,changes:changes()});const data=await r.json();
      if(!r.ok)throw new Error(data.error||'没能保存。');
      await loadMemoryCatalog(false);const after=$('memory-detail').querySelector('.memory-status');if(after)after.textContent=data.message||'已保存。';
    }catch(error){status.textContent=error.message||'没能保存。';sync()}
  };
  let armed=null;
  remove.onclick=async()=>{
    if(!armed){remove.textContent='确定删除？再点一次';remove.classList.add('armed');armed=setTimeout(()=>{armed=null;remove.textContent='删除这条';remove.classList.remove('armed')},4000);return}
    clearTimeout(armed);armed=null;remove.disabled=true;status.textContent='正在删除…';
    try{const r=await api('/api/memory/delete',{id:item.memory_id});const data=await r.json();
      if(!r.ok)throw new Error(data.error||'没能删除。');
      delete detail.dataset.memoryId;const done=document.createElement('p');done.className='muted';done.textContent='已删除「'+item.title+'」。删了不能恢复';
      detail.replaceChildren(done);await loadMemoryCatalog(false);
    }catch(error){status.textContent=error.message||'没能删除。';remove.disabled=false;remove.textContent='删除这条';remove.classList.remove('armed')}
  };
  const raw=document.createElement('details');raw.className='memory-raw';
  const summary=document.createElement('summary');summary.textContent='原始文件';const pre=document.createElement('pre');raw.append(summary,pre);
  raw.ontoggle=async()=>{if(!raw.open||pre.dataset.loaded)return;pre.textContent='正在读取…';
    try{pre.textContent=await readRawMemory(item.memory_id);pre.dataset.loaded='true'}catch(error){pre.textContent=error.message||'这条记忆读不了'}};
  const hint=document.createElement('p');hint.className='memory-hint';hint.textContent='保存留旧版，删除不可恢复';
  // Ticket 363: links, lock, earlier versions, and a flag when he rewrote something Lan had corrected.
  const lock=document.createElement('button');lock.type='button';lock.textContent=item.locked?'解锁':'锁定（他不能改）';actions.append(lock);
  lock.onclick=async()=>{lock.disabled=true;try{const r=await api('/api/memory/lock',{id:item.memory_id,locked:!item.locked});const d=await r.json();
    if(!r.ok)throw new Error(d.error||'没能保存。');await loadMemoryCatalog(false);const after=$('memory-detail').querySelector('.memory-status');if(after)after.textContent=d.message||''}
    catch(error){status.textContent=error.message||'没能保存。';lock.disabled=false}};
  const extra=[];
  if(item.peter_after_lan){const flag=document.createElement('p');flag.className='memory-surprise';flag.textContent='他又改了，可恢复或锁定';
    const ok=document.createElement('button');ok.type='button';ok.textContent='知道了';ok.onclick=async()=>{await api('/api/memory/seen',{id:item.memory_id});await loadMemoryCatalog(false)};flag.append(' ',ok);extra.push(flag)}
  const relations=[];
  for(const link of item.links||[])relations.push('接着「'+link.title+'」');
  for(const link of item.continued_by||[])relations.push('后来的「'+link.title+'」');
  if(item.merged_from)relations.push('并进来 '+item.merged_from+' 条重复（已归档）');
  if(Array.isArray(item.sources)&&item.sources.length){const last=new Date(item.sources[item.sources.length-1].time);if(!Number.isNaN(last.getTime()))relations.push(`最近一次写于 ${last.getMonth()+1}月${last.getDate()}日 ${memoryTime(item.sources[item.sources.length-1].time)}`)}
  if(relations.length){const rel=document.createElement('p');rel.className='memory-facts';rel.textContent=relations.join('　');extra.push(rel)}
  const history=document.createElement('details');history.className='memory-raw';
  const historySummary=document.createElement('summary');historySummary.textContent='历史版本';const historyBody=document.createElement('div');history.append(historySummary,historyBody);
  history.ontoggle=async()=>{if(!history.open||historyBody.dataset.loaded)return;historyBody.textContent='正在读取…';
    try{const r=await api('/api/memory/versions',{id:item.memory_id});const d=await r.json();if(!r.ok)throw new Error(d.error||'读取失败');
      historyBody.replaceChildren();historyBody.dataset.loaded='true';
      if(!d.versions.length){historyBody.textContent='还没有旧版本。';return}
      for(const version of d.versions){const box=document.createElement('div');box.className='memory-version';
        const when=new Date(version.time);const head=document.createElement('p');head.className='memory-facts';
        head.textContent=(Number.isNaN(when.getTime())?'':`${when.getMonth()+1}月${when.getDate()}日 ${memoryTime(version.time)} 之前的样子`)+(version.reason?' · '+version.reason:'');
        const text=document.createElement('pre');text.textContent=(version.title?version.title+'\n\n':'')+version.body;
        const back=document.createElement('button');back.type='button';back.textContent='恢复成这个版本';
        back.onclick=async()=>{back.disabled=true;try{const r2=await api('/api/memory/restore-version',{id:item.memory_id,index:version.index});const d2=await r2.json();
          if(!r2.ok)throw new Error(d2.error||'没能恢复。');await loadMemoryCatalog(false);const after=$('memory-detail').querySelector('.memory-status');if(after)after.textContent=d2.message||'已恢复。'}
          catch(error){status.textContent=error.message||'没能恢复。';back.disabled=false}};
        box.append(head,text,back);historyBody.appendChild(box)}
    }catch(error){historyBody.textContent=error.message||'读取失败'}};
  detail.replaceChildren(eyebrow,heading,facts,...extra,title.wrap,why.wrap,body.wrap,actions,status,hint,history,raw);
}
const LIFE_LABEL={event:'日历记录',note:'待办便签',plan:'长期计划',diary:'日记'};let lifeRows=[],lifeOwner='all',lifeKind='all',lifeDay='';const lifeDateKey=d=>d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');const lifeToday=()=>lifeDateKey(new Date());
let lifeRevision=0,lifeLoadRevision=0,lifeFolded=0;
// Ticket 385: both see every record; the filter only picks who wrote it. Moods are two emoji a day (Lan above, Peter below);
// daily todos keep a per-day log, so a past day shows what was ticked then and stays read-only.
let lifeMoods={},lifeChecks={started:null,days:{}},lifePicker=null,lifeRenderDeferred=false,lifeAutoRecordEnabled=true;
const lifeWriter=x=>x.created_by||x.owner||'共同';
const lifeOneOffNote=x=>x.kind==='note'&&x.repeat!=='daily';
const lifeEventOrder=(a,b)=>(a.time||'99:99').localeCompare(b.time||'99:99')||String(a.title).localeCompare(String(b.title));
async function loadLife(){const revision=lifeRevision,loadRevision=++lifeLoadRevision;try{const r=await api('/api/life');const d=await r.json();if(!r.ok)throw Error(d.error||'读取失败');if(revision!==lifeRevision||loadRevision!==lifeLoadRevision)return;lifeRows=d.records||[];lifeFolded=d.folded||0;lifeMoods=d.moods&&typeof d.moods==='object'?d.moods:{};lifeChecks=d.daily_checks&&typeof d.daily_checks==='object'?{started:d.daily_checks.started||null,days:d.daily_checks.days||{}}:{started:null,days:{}};lifeAutoRecordEnabled=d.auto_record_enabled!==false;const auto=$('life-auto-record');if(auto)auto.textContent='聊天约定自动记日历：'+(lifeAutoRecordEnabled?'开':'关');
  // An open emoji panel is not torn down by the 15-second refresh; the page catches up when it closes.
  if(lifePicker){lifeRenderDeferred=true;return}renderLife()}catch(e){if(revision!==lifeRevision||loadRevision!==lifeLoadRevision)return;lifeRows=[];lifeFolded=0;if(lifePicker){lifeRenderDeferred=true;return}renderLife();$('life-count').textContent=e.message||'生活记录暂不可用'}}
function lifeCalendar(selected,records){
  const month=new Date(selected.getFullYear(),selected.getMonth(),1),start=new Date(month);start.setDate(1-((month.getDay()+6)%7));
  const grid=document.createElement('div');grid.className='life-calendar';
  const heading=document.createElement('div');heading.className='life-calendar-heading';
  const prev=document.createElement('button');prev.type='button';prev.setAttribute('aria-label','上个月');prev.textContent='‹';
  const next=document.createElement('button');next.type='button';next.setAttribute('aria-label','下个月');next.textContent='›';
  const label=document.createElement('strong');label.textContent=month.toLocaleDateString('zh-CN',{year:'numeric',month:'long'});
  const today=document.createElement('button');today.type='button';today.className='calendar-today';today.textContent='今天';today.onclick=()=>{lifeDay=lifeToday();$('life-day').value=lifeDay;renderLife()};
  prev.onclick=()=>{lifeDay=lifeDateKey(new Date(month.getFullYear(),month.getMonth()-1,1));$('life-day').value=lifeDay;renderLife()};
  next.onclick=()=>{lifeDay=lifeDateKey(new Date(month.getFullYear(),month.getMonth()+1,1));$('life-day').value=lifeDay;renderLife()};
  const monthNav=document.createElement('div');monthNav.className='calendar-month-nav';monthNav.append(prev,label,next);heading.append(monthNav,today);grid.append(heading);
  const days=document.createElement('div');days.className='life-calendar-days';
  for(const name of ['一','二','三','四','五','六','日']){const cell=document.createElement('span');cell.textContent=name;days.append(cell)}
  for(let i=0;i<42;i++){
    const date=new Date(start);date.setDate(start.getDate()+i);const key=lifeDateKey(date),cell=document.createElement('button');cell.type='button';cell.className=date.getMonth()===month.getMonth()?'':'outside';
    const number=document.createElement('span');number.className='calendar-day-number';number.textContent=String(date.getDate());cell.append(number);
    // A dot only where someone wrote something for that day: an event, a one-off todo, or a mood. Daily todos never add one.
    const count=records.filter(x=>(x.kind==='event'||lifeOneOffNote(x))&&x.state!=='cancelled'&&x.date===key).length,mood=lifeMoodsFor(key),moodText=[mood.Lan&&'Lan '+mood.Lan.emoji,mood.Peter&&'Peter '+mood.Peter.emoji].filter(Boolean);
    // Under the number: the two moods in one line (Lan, then Peter), with the dot after them when something is written.
    if(count||moodText.length){cell.classList.add('has-record');const line=document.createElement('span');line.className='calendar-day-line';line.setAttribute('aria-hidden','true');
      for(const person of ['Lan','Peter'])if(mood[person]){const face=document.createElement('span');face.className='calendar-mood '+person.toLowerCase();face.textContent=mood[person].emoji;line.append(face)}
      if(count){const marker=document.createElement('span');marker.className='calendar-day-marker';line.append(marker)}cell.append(line)}
    if(key===lifeToday())cell.classList.add('today');if(key===lifeDay)cell.classList.add('selected');
    const aria=[date.toLocaleDateString('zh-CN',{year:'numeric',month:'long',day:'numeric'}),count?`${count} 项安排或待办`:'',moodText.length?'心情 '+moodText.join('、'):''].filter(Boolean).join('，');
    cell.setAttribute('aria-label',aria);cell.setAttribute('aria-pressed',String(key===lifeDay));cell.onclick=()=>{lifeDay=key;$('life-day').value=key;renderLife()};days.append(cell)
  }
  grid.append(days);return grid
}
function lifeMoodsFor(day){const slot=lifeMoods[day];return slot&&typeof slot==='object'?slot:{}}
function lifeAgendaRow(x,kind){const row=document.createElement('div');row.className='life-agenda-row';if(x.state==='cancelled')row.style.textDecoration='line-through';if(kind==='note'){const done=x.state==='completed';const box=document.createElement('button');box.type='button';box.className='agenda-check';box.textContent=done?'✓':'';box.setAttribute('aria-label',(done?'改回未完成：':'标记完成：')+x.title);box.onclick=()=>saveLife({id:x.id,state:done?'active':'completed'},true);row.append(box)}else{const dot=document.createElement('span');dot.className='agenda-dot';row.append(dot)}if(kind==='event'){const time=document.createElement('span');time.className='agenda-time';time.textContent=(x.time||'全天')+(x.remind?' ⏰':'');row.append(time)}const title=document.createElement('button');title.type='button';title.className='agenda-title';title.textContent=x.title;title.onclick=()=>showLife(x);row.append(title);const owner=document.createElement('span');owner.className='agenda-owner';owner.textContent=lifeWriter(x);owner.title=lifeWriter(x)+' 写的';row.append(owner);if(x.kind==='note'&&!x.source&&!x.read_only){const del=document.createElement('button');del.type='button';del.className='agenda-delete';del.textContent='×';del.title='删除这一条';del.onclick=()=>armDelete(del,x);row.append(del)}return row}
// Mood slots for the selected day: clicking the date only selects it; clicking a slot (empty too) opens the panel.
function lifeMoodSection(day,today){
  const section=document.createElement('section');section.className='agenda-group agenda-moods';const title=document.createElement('h3');title.textContent='心情';section.append(title);
  const slots=document.createElement('div');slots.className='mood-slots';const mood=lifeMoodsFor(day),future=day>today;
  for(const person of ['Lan','Peter']){
    const slot=document.createElement('button');slot.type='button';slot.className='mood-slot '+person.toLowerCase();slot.dataset.person=person;
    const face=document.createElement('span');face.className='mood-slot-emoji';face.textContent=mood[person]?.emoji||'＋';if(!mood[person])face.classList.add('empty');
    const name=document.createElement('small');name.textContent=person==='Lan'?'你':'Peter';slot.append(face,name);
    if(future){slot.disabled=true;slot.title='还没到这一天'}
    slot.setAttribute('aria-label',(person==='Lan'?'你的心情':'Peter 的心情')+'：'+(mood[person]?.emoji||'还没贴'));
    slot.onclick=()=>person==='Lan'?openMoodPicker(day,section):lifeMoodHint(section,'Peter 的心情');
    slots.append(slot)
  }
  section.append(slots);if(future){const hint=document.createElement('p');hint.className='agenda-empty';hint.textContent='到那天才能贴';section.append(hint)}
  if(lifePicker&&lifePicker.day===day)openMoodPicker(day,section,lifePicker);
  return section
}
function lifeMoodHint(section,text){if(!section.isConnected)section=document.querySelector('.agenda-moods')||section;let hint=section.querySelector('.mood-hint');if(!hint){hint=document.createElement('p');hint.className='mood-hint';hint.setAttribute('role','status');section.append(hint)}hint.textContent=text;clearTimeout(lifeMoodHint.timer);lifeMoodHint.timer=setTimeout(()=>hint.remove(),3000)}
const MOOD_CATEGORIES=[['开心','😀😄😊😆🤗😌🥳'],['爱','😍🥰😘💕❤️🫶💋'],['难过','😢😭🥺😔😞💔🫠'],['生气','😠😤😡🙄😒💢🤬'],['累','😴🥱😪😩😮‍💨🤒🤯'],['其他','🤔😶😳🙃🤪😎👀']];
const lifeSplitEmoji=text=>{try{return [...new Intl.Segmenter('zh',{granularity:'grapheme'}).segment(text)].map(x=>x.segment)}catch(e){return Array.from(text)}};
function lifeRecentEmoji(){try{const list=JSON.parse(localStorage.getItem('veil-life-recent-emoji')||'[]');return Array.isArray(list)?list.filter(x=>typeof x==='string'&&x).slice(0,16):[]}catch(e){return []}}
function lifeRememberEmoji(emoji){try{localStorage.setItem('veil-life-recent-emoji',JSON.stringify([emoji,...lifeRecentEmoji().filter(x=>x!==emoji)].slice(0,16)))}catch(e){}}
function closeMoodPicker(){const open=document.querySelector('.mood-picker');if(open)open.remove();document.removeEventListener('pointerdown',moodPickerOutside,true);document.removeEventListener('keydown',moodPickerKey,true);lifePicker=null;if(lifeRenderDeferred){lifeRenderDeferred=false;renderLife()}}
function moodPickerOutside(event){const picker=document.querySelector('.mood-picker');if(picker&&!picker.contains(event.target)&&!event.target.closest?.('.mood-slot.lan'))closeMoodPicker()}
function moodPickerKey(event){if(event.key==='Escape'){event.preventDefault();closeMoodPicker()}}
async function saveMood(day,emoji,picker){
  const status=picker.querySelector('.mood-picker-status');status.textContent='正在保存…';picker.querySelectorAll('button').forEach(b=>b.disabled=true);
  lifeRevision++;
  try{const r=await api('/api/life',{mood:{date:day,emoji}});const d=await r.json();if(!r.ok)throw Error(d.error||'心情没存上');
    const slot=d.moods&&typeof d.moods==='object'?d.moods:{};if(Object.keys(slot).length)lifeMoods[day]=slot;else delete lifeMoods[day];
    if(emoji)lifeRememberEmoji(emoji);closeMoodPicker();renderLife()}
  catch(e){status.textContent=e.message==='mood must be one emoji'?'不是 emoji，换一个':e.message||'心情没存上';picker.querySelectorAll('button').forEach(b=>b.disabled=false)}
}
function openMoodPicker(day,section,restore=null){
  document.querySelector('.mood-picker')?.remove();
  const current=lifeMoodsFor(day).Lan?.emoji||'',state=restore||{day,tab:0,more:false,group:0,query:''};lifePicker=state;
  const picker=document.createElement('div');picker.className='mood-picker';picker.setAttribute('role','dialog');picker.setAttribute('aria-label','选今天的心情');
  const head=document.createElement('header');const title=document.createElement('strong');title.textContent='你的心情';const hint=document.createElement('small');hint.textContent=current?'再点一次取消':'选一个就保存';
  const close=document.createElement('button');close.type='button';close.className='mood-picker-close';close.textContent='×';close.setAttribute('aria-label','关闭');close.onclick=closeMoodPicker;head.append(title,hint,close);picker.append(head);
  const button=(emoji,name)=>{const b=document.createElement('button');b.type='button';b.className='mood-choice';b.textContent=emoji;b.title=name||emoji;const on=emoji===current;b.setAttribute('aria-pressed',String(on));b.setAttribute('aria-label',(on?'清除心情 ':'选 ')+(name||emoji));b.onclick=()=>saveMood(day,on?'':emoji,picker);return b};
  const recent=lifeRecentEmoji();
  if(recent.length){const row=document.createElement('div');row.className='mood-row mood-recent';const label=document.createElement('small');label.textContent='最近用过';row.append(label);for(const e of recent)row.append(button(e));picker.append(row)}
  const tabs=document.createElement('div');tabs.className='mood-tabs';tabs.setAttribute('role','tablist');const grid=document.createElement('div');grid.className='mood-row mood-grid';
  const showTab=index=>{state.tab=index;tabs.querySelectorAll('button').forEach((b,i)=>b.setAttribute('aria-selected',String(i===index)));grid.replaceChildren(...lifeSplitEmoji(MOOD_CATEGORIES[index][1]).map(e=>button(e)))};
  MOOD_CATEGORIES.forEach(([name],index)=>{const tab=document.createElement('button');tab.type='button';tab.setAttribute('role','tab');tab.textContent=name;tab.onclick=()=>showTab(index);tabs.append(tab)});
  picker.append(tabs,grid);showTab(state.tab);
  const moreToggle=document.createElement('button');moreToggle.type='button';moreToggle.className='mood-more-toggle';const more=document.createElement('div');more.className='mood-more';
  const fillMore=()=>{
    more.replaceChildren();const groups=Array.isArray(window.VEIL_EMOJI)?window.VEIL_EMOJI:[];
    const search=document.createElement('input');search.type='search';search.className='mood-search';search.placeholder='搜索英文名';search.value=state.query;
    const groupTabs=document.createElement('div');groupTabs.className='mood-tabs mood-group-tabs';const all=document.createElement('div');all.className='mood-row mood-all';
    const paint=()=>{const query=state.query.trim().toLowerCase();groupTabs.hidden=!!query;groupTabs.querySelectorAll('button').forEach((b,i)=>b.setAttribute('aria-selected',String(i===state.group)));
      const items=query?groups.flatMap(g=>g[1]).filter(([e,name])=>name.includes(query)||e===query).slice(0,240):(groups[state.group]?.[1]||[]);
      all.replaceChildren(...items.map(([e,name])=>button(e,name)));if(!items.length){const empty=document.createElement('p');empty.className='agenda-empty';empty.textContent=groups.length?'没找到，可直接粘贴':'列表没加载，可直接粘贴';all.append(empty)}};
    groups.forEach(([name],index)=>{const tab=document.createElement('button');tab.type='button';tab.textContent=name;tab.onclick=()=>{state.group=index;paint()};groupTabs.append(tab)});
    search.oninput=()=>{state.query=search.value;paint()};
    const own=document.createElement('form');own.className='mood-own';const input=document.createElement('input');input.placeholder='或粘贴任意 emoji';input.maxLength=16;input.setAttribute('aria-label','粘贴任意 emoji');const save=document.createElement('button');save.type='submit';save.textContent='贴上';own.append(input,save);
    own.onsubmit=event=>{event.preventDefault();const value=input.value.trim();if(!value)return;const parts=lifeSplitEmoji(value);if(parts.length!==1){picker.querySelector('.mood-picker-status').textContent='一次贴一个 emoji';return}saveMood(day,value,picker)};
    more.append(search,groupTabs,all,own);paint()};
  const syncMore=()=>{moreToggle.textContent=state.more?'收起 emoji':'更多 emoji';moreToggle.setAttribute('aria-expanded',String(state.more));more.hidden=!state.more;if(state.more&&!more.childElementCount)fillMore()};
  moreToggle.onclick=()=>{state.more=!state.more;syncMore();if(state.more)more.scrollIntoView({block:'nearest'})};picker.append(moreToggle,more);syncMore();
  const status=document.createElement('p');status.className='mood-picker-status';status.setAttribute('role','status');picker.append(status);
  section.append(picker);document.addEventListener('pointerdown',moodPickerOutside,true);document.addEventListener('keydown',moodPickerKey,true);
  if(!restore)picker.querySelector('.mood-tabs button')?.focus()
}
// Daily todos stay in one fixed section that follows the selected day: today can be ticked, a past day shows that
// day's log read-only ("没有记录" when nothing tells us), and a future day shows empty circles that cannot be ticked.
function lifeDailySection(dailies,day,today){
  const section=document.createElement('section');section.className='agenda-group agenda-daily';const head=document.createElement('header');head.className='agenda-group-heading';const title=document.createElement('h3');title.textContent='每日待办';const count=document.createElement('span');head.append(title,count);section.append(head);
  const log=lifeChecks.days?.[day]||{},logged=!!lifeChecks.started&&day>=lifeChecks.started;let done=0,shown=0;
  for(const x of dailies){
    if(day<today&&(x.created_at||'').slice(0,10)>day&&!log[x.id])continue;shown++;
    const row=document.createElement('div');row.className='life-agenda-row daily-row';const box=document.createElement('button');box.type='button';box.className='agenda-check';
    const label=document.createElement('button');label.type='button';label.className='agenda-title';label.textContent=x.title;label.onclick=()=>showLife(x);
    const note=document.createElement('span');note.className='agenda-owner';
    if(day===today){const finished=x.state==='completed';if(finished)done++;box.textContent=finished?'✓':'';box.setAttribute('aria-label',(finished?'改回未完成：':'标记完成：')+x.title);box.onclick=()=>saveLife({id:x.id,state:finished?'active':'completed'},true)}
    else if(day>today){box.disabled=true;box.setAttribute('aria-label','还没到这一天：'+x.title);row.classList.add('future')}
    else{box.disabled=true;row.classList.add('past');const entry=log[x.id];
      if(entry&&entry.state==='completed'){done++;box.textContent='✓';const at=String(entry.at||'');note.textContent=(at.length>=16?at.slice(11,16)+' ':'')+(entry.by?entry.by+' 勾的':'勾了')+(at.slice(0,10)>day?' · 过了 0 点补的':'')+(entry.reconstructed?'（按旧记录补）':'');box.setAttribute('aria-label','那天完成了：'+x.title)}
      else if(entry||logged){box.setAttribute('aria-label','那天没有勾：'+x.title)}
      else{box.classList.add('unknown');box.textContent='–';note.textContent='没有记录';box.setAttribute('aria-label','那天没有记录：'+x.title)}}
    row.append(box,label,note);section.append(row)
  }
  count.textContent=day>today?`${shown} 项 · 还没到`:day<today?`${done} / ${shown} · 只能看`:`${done} / ${shown}`;
  if(!shown){const empty=document.createElement('p');empty.className='agenda-empty';empty.textContent=dailies.length?'那天没有这些待办':'还没有每日待办';section.append(empty)}
  if(day===today){const add=document.createElement('button');add.type='button';add.className='agenda-add';add.textContent='＋ 写一条每日待办';add.onclick=()=>lifeForm({kind:'note',date:today,repeat:'daily'});section.append(add)}
  return section
}
function renderLife(){
  const host=$('life-records'),today=lifeToday();host.replaceChildren();if(!lifeDay)lifeDay=today;$('life-day').value=lifeDay;
  const selected=new Date(lifeDay+'T12:00:00'),selectedLabel=selected.toLocaleDateString('zh-CN',{month:'long',day:'numeric'}),ownerFilter=x=>lifeOwner==='all'||lifeWriter(x)===lifeOwner;
  const relevant=lifeRows.filter(x=>x.kind!=='diary'&&ownerFilter(x)),events=relevant.filter(x=>x.kind==='event'&&x.date===lifeDay).sort(lifeEventOrder),dailies=relevant.filter(x=>x.kind==='note'&&x.repeat==='daily'),notes=relevant.filter(x=>lifeOneOffNote(x)&&(x.date===lifeDay||x.date<lifeDay&&x.state!=='completed'&&lifeDay>=today||lifeDay===today&&(x.completed_at||'').slice(0,10)===today)),plans=relevant.filter(x=>x.kind==='plan'&&x.state==='active');
  if(lifeKind==='all'||lifeKind==='event'){
    $('life-count').textContent=lifeKind==='all'?`${events.length} 项安排 · ${notes.filter(x=>x.state==='completed').length}/${notes.length} 项待办完成`:`${events.length} 项安排`;
    host.classList.toggle('life-overview',lifeKind==='all');
    host.append(lifeCalendar(selected,relevant));
    if(lifeKind==='all'){
      const agenda=document.createElement('section');agenda.className='life-agenda';
      const dateHeading=document.createElement('header');dateHeading.className='agenda-date';const dateTitle=document.createElement('strong');dateTitle.textContent=selectedLabel;const weekday=document.createElement('span');weekday.textContent=selected.toLocaleDateString('zh-CN',{weekday:'long'});dateHeading.append(dateTitle,weekday);agenda.append(dateHeading);
      agenda.append(lifeMoodSection(lifeDay,today));
      const eventGroup=document.createElement('section');eventGroup.className='agenda-group';const eventTitle=document.createElement('h3');eventTitle.textContent='日程';eventGroup.append(eventTitle);
      for(const x of events)eventGroup.append(lifeAgendaRow(x,'event'));
      if(!events.length){const empty=document.createElement('p');empty.className='agenda-empty';empty.textContent=lifeDay===today?'今天没有安排':'这天没有安排';eventGroup.append(empty)}
      const addEvent=document.createElement('button');addEvent.type='button';addEvent.className='agenda-add';addEvent.textContent='＋ 写一条日程';addEvent.onclick=()=>lifeForm({kind:'event',date:lifeDay});eventGroup.append(addEvent);
      agenda.append(eventGroup);
      const todoGroup=document.createElement('section');todoGroup.className='agenda-group';const todoHeader=document.createElement('header');todoHeader.className='agenda-group-heading';const todoTitle=document.createElement('h3');todoTitle.textContent='待办';const todoCount=document.createElement('span');todoCount.textContent=`${notes.filter(x=>x.state==='completed').length} / ${notes.length}`;todoHeader.append(todoTitle,todoCount);todoGroup.append(todoHeader);
      for(const x of notes)todoGroup.append(lifeAgendaRow(x,'note'));
      if(!notes.length){const empty=document.createElement('p');empty.className='agenda-empty';empty.textContent='这天没有一次性待办';todoGroup.append(empty)}
      const add=document.createElement('button');add.type='button';add.className='agenda-add';add.textContent='＋ 写一条待办';add.onclick=()=>lifeForm({kind:'note',date:lifeDay});todoGroup.append(add);agenda.append(todoGroup);
      if(plans.length){const planGroup=document.createElement('section');planGroup.className='agenda-group';const title=document.createElement('h3');title.textContent='长期计划';planGroup.append(title);for(const x of plans)planGroup.append(lifeAgendaRow(x,'plan'));agenda.append(planGroup)}
      // The daily section sits under the scrolling agenda, so it stays in view on every day without covering anything.
      const panel=document.createElement('div');panel.className='life-day-panel';panel.append(agenda,lifeDailySection(dailies,lifeDay,today));
      host.append(panel);
    }else for(const x of events){const row=document.createElement('button');row.type='button';row.className='life-card';const title=document.createElement('strong');title.textContent=x.title;const meta=document.createElement('span');meta.textContent=`${lifeWriter(x)} 写的 · ${x.date}${x.time?' '+x.time:''}`;row.append(title,meta);row.onclick=()=>showLife(x);host.append(row)}
    return
  }
  host.classList.remove('life-overview');
  const rows=relevant.filter(x=>lifeKind==='completed'?x.state==='completed':x.kind===lifeKind&&(lifeKind==='note'||lifeKind==='plan'||!lifeDay||x.date===lifeDay));
  $('life-count').textContent=`${rows.length} 条记录`+(lifeFolded&&(lifeKind==='note'||lifeKind==='completed')?` · 另有 ${lifeFolded} 条旧便签已折叠`:'');
  const stickyRows=lifeKind==='note'?rows.filter(x=>x.kind==='note'):[];
  if(stickyRows.length||lifeKind==='note'){const once=stickyRows.filter(x=>x.repeat!=='daily');host.append(lifeSticky(stickyRows.filter(x=>x.repeat==='daily'),today,today,true));const dates=[...new Set([today,...once.map(x=>x.date)])].sort().reverse();for(const day of dates)host.append(lifeSticky(once.filter(x=>x.date===day),day,today,false))}
  for(const x of rows.filter(x=>!stickyRows.includes(x))){const b=document.createElement('button');b.className='life-card';const title=document.createElement('strong');title.textContent=x.title;const meta=document.createElement('span');meta.textContent=`${LIFE_LABEL[x.kind]} · ${lifeWriter(x)} 写的 · ${x.date}${x.time?' '+x.time:''}`;const state=document.createElement('span');state.className='life-state';state.textContent=({completed:'已完成',paused:'已搁置',blocked:'受阻',expired:'已过期'})[x.state]||'进行中';b.append(title,meta,state);b.onclick=()=>showLife(x);host.append(b)}
}
function lifeSticky(items,day,today,daily){const note=document.createElement('section');note.className='life-sticky';const d=new Date(day+'T12:00:00');const head=document.createElement('header');const big=document.createElement('b');big.textContent=daily?'每日':String(d.getMonth()+1).padStart(2,'0')+'.'+String(d.getDate()).padStart(2,'0');const sub=document.createElement('span');sub.textContent=daily?'每天 0 点自动清空':'周'+'日一二三四五六'[d.getDay()]+' · 一起写的那张';const done=items.filter(x=>x.state==='completed').length;const cnt=document.createElement('em');cnt.textContent=items.length?done+' / '+items.length:'';head.append(big,sub,cnt);const title=document.createElement('h3');title.textContent=daily?'每日重复':day===today?'今日待办':'当天待办';const list=document.createElement('ul');for(const x of items){const li=document.createElement('li');const finished=x.state==='completed';li.classList.toggle('done',finished);const box=document.createElement('button');box.type='button';box.className='sticky-box';box.textContent=finished?'✓':'';box.setAttribute('aria-label',(finished?'改回未完成：':'标记完成：')+x.title);box.onclick=()=>saveLife({id:x.id,state:finished?'active':'completed'},true);const text=document.createElement('button');text.type='button';text.className='sticky-text';text.textContent=x.title;text.title=lifeWriter(x)+' 写的 · '+x.date;text.onclick=()=>showLife(x);const del=document.createElement('button');del.type='button';del.className='sticky-del';del.textContent='×';del.title='删除这一条';del.onclick=()=>armDelete(del,x);li.append(box,text,del);list.append(li)}if(!items.length){const empty=document.createElement('p');empty.className='sticky-empty';empty.textContent=daily?'还没有每日待办':'还没有待办';list.append(empty)}const form=document.createElement('form');form.className='sticky-add';const input=document.createElement('input');input.maxLength=200;input.placeholder=daily?'写每天要做的事，回车添加':'写今天要做的事，回车添加';form.append(input);form.onsubmit=ev=>{ev.preventDefault();const v=input.value.trim();if(!v)return;input.value='';saveLife({kind:'note',title:v,date:today,repeat:daily?'daily':'none'},true).then(ok=>{if(ok===false&&!input.value)input.value=v},()=>{if(!input.value)input.value=v;notify('保存失败','error')})};note.append(head,title,list);if(daily||day===today)note.append(form);return note}
function armDelete(btn,x){if(btn.dataset.armed){delete btn.dataset.armed;btn.textContent=btn.className==='sticky-del'?'×':'删除';saveLife({id:x.id,delete:true},true).then(ok=>{if(ok)$('life-detail').classList.remove('open')},()=>notify('删除失败','error'));return}btn.dataset.armed='1';btn.textContent='确认删除';setTimeout(()=>{if(btn.isConnected&&btn.dataset.armed){delete btn.dataset.armed;btn.textContent=btn.className==='sticky-del'?'×':'删除'}},3000)}
const LIFE_STATE_LABEL={active:'进行中',completed:'已完成',paused:'已搁置',cancelled:'已取消',blocked:'受阻',expired:'已过期'};
function showLife(x){$('life-detail').classList.add('open');$('life-detail').innerHTML='<div class="detail-top"><button class="life-close" type="button">关闭</button></div><h2 class="detail-title"></h2><p class="detail-date"></p><div class="detail-content"></div><p class="detail-mood" hidden></p><div class="detail-audit"></div><div class="detail-history"></div><div class="detail-actions"><button id="life-edit">编辑</button><button id="life-done">标记完成</button><button id="life-delete">删除</button></div>';const d=$('life-detail');d.querySelector('.detail-top').prepend(document.createTextNode(LIFE_LABEL[x.kind]+' · '+lifeWriter(x)+' 写的'));d.querySelector('.life-close').onclick=()=>d.classList.remove('open');d.querySelector('.detail-title').textContent=x.title;d.querySelector('.detail-date').textContent=(x.repeat==='daily'?'每天':x.date)+(x.kind==='event'?' · '+(x.time||'全天'):'')+' · '+(LIFE_STATE_LABEL[x.state]||x.state);d.querySelector('.detail-content').textContent=x.content;
  // Old records may still carry the per-record mood text the form used to ask for; it stays readable here.
  if(x.mood){const mood=d.querySelector('.detail-mood');mood.hidden=false;mood.textContent='当时写的心情：'+x.mood}
  if(x.auto_recorded||x.remind||x.reminder_missed){const info=document.createElement('p');info.textContent=[x.auto_recorded?'自动记的':'',x.remind?(x.remind_before?'提前 '+x.remind_before+' 分钟提醒':'准点提醒'):'',x.reminder_missed?'提醒错过了 '+x.reminder_missed:''].filter(Boolean).join(' · ');d.querySelector('.detail-audit').before(info)}
  d.querySelector('.detail-audit').textContent='由 '+x.created_by+' 创建 · 最近由 '+x.updated_by+' 修改于 '+x.updated_at+(x.completed_at?' · 完成于 '+x.completed_at:'')+(x.history?.length?' · 历史版本 '+x.history.length+' 条':'');if(x.history?.length){const h=d.querySelector('.detail-history');const title=document.createElement('h3');title.textContent='修改历史';h.append(title);for(const item of [...x.history].reverse()){const row=document.createElement('div');row.textContent=[item.at||'',item.by?item.by+' 写的版本':'',item.title||'',item.date?item.date+(item.time?' '+item.time:''):'',LIFE_STATE_LABEL[item.state]||item.state||''].filter(Boolean).join(' · ');h.append(row)}}d.querySelector('#life-edit').disabled=x.state==='completed'||x.state==='expired'||!!x.read_only&&x.source!=='long_task_update'&&x.source!=='short_term_notes';d.querySelector('#life-done').disabled=x.state==='completed'||x.state==='expired'||!!x.read_only&&x.source!=='Ombre plan'&&x.source!=='long_task_update'&&x.source!=='short_term_notes';d.querySelector('#life-edit').onclick=()=>lifeForm(x);const delBtn=d.querySelector('#life-delete');if(x.source||x.read_only||x.kind==='plan')delBtn.remove();else delBtn.onclick=()=>armDelete(delBtn,x);d.querySelector('#life-done').onclick=()=>{if(x.source==='long_task_update'){const evidence=prompt('填写完成依据：');if(!evidence)return;saveLife({id:x.id,state:'completed',progress_update:evidence});return}saveLife({id:x.id,state:'completed'})}}
// The form no longer asks for a mood or an owner: who wrote it is filled in automatically. Events may take a time.
function lifeForm(x={}){const d=$('life-detail');d.classList.add('open');d.innerHTML='<button class="life-close" type="button">关闭</button><h2>写下一条记录</h2><label class="life-field">类型<select id="lf-kind"><option value="event">日历记录</option><option value="note">待办便签</option><option value="plan">长期计划</option></select></label><label class="life-field">标题<input id="lf-title"></label><label class="life-field">内容<textarea id="lf-content" rows="7"></textarea></label><label class="life-field">日期<input id="lf-date" type="date"></label><label class="life-field" id="lf-time-field">几点（可不填，不填就是全天）<input id="lf-time" type="time" step="60"></label><label class="life-field" id="lf-remind-field"><input id="lf-remind" type="checkbox"> 提醒我 <select id="lf-remind-before"><option value="0">准点</option><option value="15">提前 15 分钟</option><option value="30">提前 30 分钟</option></select><small id="lf-remind-hint">填了几点才能提醒</small></label><label class="life-field">每天重复（0 点自动变回未完成）<select id="lf-repeat"><option value="none">不重复</option><option value="daily">每天重复</option></select></label><p class="life-form-writer"></p><button id="lf-save" class="life-add">保存记录</button>';d.querySelector('.life-close').onclick=()=>d.classList.remove('open');d.querySelector('#lf-kind').value=x.kind||'event';for(const id of ['lf-kind','lf-date'])d.querySelector('#'+id).disabled=!!x.source;d.querySelector('#lf-title').value=x.title||'';d.querySelector('#lf-title').disabled=x.source==='short_term_notes';d.querySelector('#lf-content').value=x.content||'';d.querySelector('#lf-date').value=x.date||lifeToday();d.querySelector('#lf-time').value=x.time||'';d.querySelector('#lf-remind').checked=!!x.remind;d.querySelector('#lf-remind-before').value=String(x.remind_before||0);d.querySelector('.life-form-writer').textContent=x.id?lifeWriter(x)+' 写的这一条':'保存后算你写的';
  const syncKind=()=>{const kind=d.querySelector('#lf-kind').value,note=kind==='note';d.querySelector('#lf-repeat').disabled=!note||!!x.source;if(!note)d.querySelector('#lf-repeat').value='none';const event=kind==='event';d.querySelector('#lf-time-field').hidden=!event;d.querySelector('#lf-time').disabled=!event||!!x.source;d.querySelector('#lf-remind-field').hidden=!event;const available=event&&!!d.querySelector('#lf-time').value&&!x.source;d.querySelector('#lf-remind').disabled=!available;d.querySelector('#lf-remind-before').disabled=!available;d.querySelector('#lf-remind-hint').hidden=available;if(!available)d.querySelector('#lf-remind').checked=false};d.querySelector('#lf-kind').onchange=syncKind;d.querySelector('#lf-time').onchange=syncKind;d.querySelector('#lf-repeat').value=x.repeat||'none';syncKind();
  d.querySelector('#lf-save').onclick=()=>{if(x.source==='short_term_notes'){saveLife({id:x.id,content:d.querySelector('#lf-content').value});return}if(x.source==='long_task_update'){saveLife({id:x.id,title:d.querySelector('#lf-title').value,content:d.querySelector('#lf-content').value});return}
    const kind=d.querySelector('#lf-kind').value,date=d.querySelector('#lf-date').value,time=kind==='event'?d.querySelector('#lf-time').value:'';
    if(time&&!date){notify('写时间也要写日期','error');return}if(time&&!/^([01]\d|2[0-3]):[0-5]\d$/.test(time)){notify('24 小时制，如 09:30','error');return}
    saveLife({...(x.id?{id:x.id}:{}),kind,title:d.querySelector('#lf-title').value,content:d.querySelector('#lf-content').value,date,time,repeat:d.querySelector('#lf-repeat').value,remind:kind==='event'&&!!time&&d.querySelector('#lf-remind').checked,remind_before:Number(d.querySelector('#lf-remind-before').value)})}}
async function saveLife(x,quiet){lifeRevision++;let r,d;try{r=await api('/api/life',x);d=await r.json()}catch(e){notify('结果未确认，刷新生活页核对','error');await loadLife();return null}if(r.status===0){notify('结果未确认，刷新生活页核对','error');await loadLife();return null}if(!r.ok){notify(d.error||'保存失败','error');return false}if(!d.record){await loadLife();return true}const i=lifeRows.findIndex(y=>y.id===d.record.id);if(i<0)lifeRows.unshift(d.record);else lifeRows[i]=d.record;renderLife();if(quiet){await loadLife();return true}showLife(d.record);return true}
// A grey line in chat says Peter changed one of Lan's records; clicking it opens that record on the life page.
// When the life page lives in its own window, or this is a detached chat window, the window that shows it opens it.
async function openLifeRecord(id){
  if((detachedViews.has('life')&&detachedView!=='life')||(detachedView&&detachedView!=='life')){window.veilDesktop.openLifeRecord?.(id);return}
  setPage('life');lifeKind='all';document.querySelectorAll('[data-life-kind]').forEach(x=>x.classList.toggle('active',x.dataset.lifeKind==='all'));
  await loadLife();let row=lifeRows.find(x=>x.id===id);
  // An old completed note can be folded out of the list; ask for it by id before saying it is gone.
  if(!row){let gone=false;try{const r=await api('/api/life',{find:id});const d=await r.json();if(r.ok&&d.found)row=d.record;else if(r.ok&&d.found===false)gone=true}catch(e){}
    if(!row){notify(gone?'这条记录已删':'读不到这条记录，再点一次','error');return}}
  if(row.date&&row.repeat!=='daily'){lifeDay=row.date;renderLife()}showLife(row)
}
window.veilDesktop.onOpenLifeRecord?.(id=>{if(typeof id==='string')openLifeRecord(id)});
const lifeAutoToggle=document.createElement('button');lifeAutoToggle.id='life-auto-record';lifeAutoToggle.type='button';lifeAutoToggle.textContent='聊天约定自动记日历：开';lifeAutoToggle.onclick=async()=>{lifeAutoToggle.disabled=true;try{const r=await api('/api/life',{auto_record:!lifeAutoRecordEnabled});const d=await r.json();if(!r.ok)throw Error(d.error||'设置失败');lifeAutoRecordEnabled=d.auto_record_enabled;lifeRevision++;lifeAutoToggle.textContent='聊天约定自动记日历：'+(lifeAutoRecordEnabled?'开':'关')}catch(err){notify(err.message,'error')}finally{lifeAutoToggle.disabled=false}};$('life-new').after(lifeAutoToggle);
document.querySelectorAll('[data-life-kind]').forEach(b=>b.onclick=()=>{lifeKind=b.dataset.lifeKind;document.querySelectorAll('[data-life-kind]').forEach(x=>x.classList.toggle('active',x===b));renderLife()});document.querySelectorAll('[data-life-owner]').forEach(b=>b.onclick=()=>{lifeOwner=b.dataset.lifeOwner;document.querySelectorAll('[data-life-owner]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));renderLife()});$('life-new').onclick=$('life-detail-new').onclick=()=>lifeForm();$('life-day').onchange=()=>{lifeDay=$('life-day').value;renderLife()};
async function approve(card,status,answer,id){
  if(card.dataset.sending==='true'||card.classList.contains('done'))return;
  card.dataset.sending='true';
  card.querySelectorAll('button').forEach(button=>button.disabled=true);
  status.textContent='正在提交…';
  try{
    const r=await api('/api/approve',{answer,id});
    if(!r.ok){
      if(r.status===409){card.classList.add('done');status.textContent='审批已失效，未执行'}
      else throw new Error(r.status);
      return;
    }
    card.classList.add('done');
    status.textContent=answer==='y'?'服务器已收到同意。':'服务器已收到拒绝。';
  }catch(e){
    status.textContent='没收到确认，核对中';
    try{
      const r=await api('/api/events?after='+after),s=await r.json();
      if(s.pending===Number(id)){
        card.querySelectorAll('button').forEach(button=>button.disabled=false);
        status.textContent='还在等选择，可重新提交';
      }else{
        card.classList.add('done');
        status.textContent='没收到确认，看后续提示';
      }
    }catch(_){
      card.querySelectorAll('button').forEach(button=>button.disabled=false);
      status.textContent='暂时无法确认，可重试';
    }
  }finally{card.dataset.sending='false'}
}
function activityDetails(record){
  const lines=[record.started_at+' → '+record.finished_at,
    '结果：'+record.result+'；'+record.result_reason,
    '决定/结束信息：'+record.decision,
    '已留存文件：'+((record.lab_files_changed||[]).join('、')||'无')];
  for(const item of record.tools||[])lines.push(item.tool+' | '+item.path+' | '+item.state+
    (item.result_state==='已截断'?'；结果截断，只给部分':
      item.result_state==='结果过长'?'；结果过长，没给模型':''));
  if(record.current_focus)lines.push('当时关注：'+record.current_focus);
  if(record.next_step)lines.push('下一步：'+record.next_step);
  lines.push('本次费用：¥'+record.cost_cny);
  return lines.join('\n');
}
const TOOL_LABELS={delegate:'委派研究',read_text_file:'读取文件',read_multiple_files:'读取多个文件',write_file:'写入文件',append_file:'追加文件',list_directory:'查看文件夹',directory_tree:'查看目录树',search_files:'查找文件',get_file_info:'查看文件信息',create_directory:'创建文件夹',move_lab_file:'移动 Lab 文件',lab_file_digest:'核对 Lab 文件',hold:'保存记忆',breath_search:'查找记忆',trace:'更新记忆',dream:'整理记忆',letter_write:'写记忆信件',letter_read:'读记忆信件',memory_history:'查看记忆历史',memory_catalog:'查看记忆目录',memory_read:'读取记忆',long_task_status:'查看长期任务',long_task_update:'更新长期任务',web_search:'联网搜索',web_fetch:'读取网页',send_message:'发送消息',screen_pop:'屏幕弹出'};
const NOWHERE_TOOL_LABELS={say:'记录乌有乡语录',quotes:'查看旅程语录',bury:'埋藏纪念品'};
function activityStatus(state,detail){
  if(state==='进行中')return {text:'进行中',className:'running'};
  if(state==='成功')return {text:'成功',className:'success'};
  // 工单 415：拒绝的原因各不相同，状态只报「被拒绝」，真实原因在展开的「执行结果」里。
  if(state==='被拒绝')return {text:'被拒绝',className:'failure'};
  if(state==='待选择')return {text:'待他选择',className:'running'};
  if(state==='出错'||state==='失败')return {text:String(detail||'').startsWith('工具内部故障')?'工具内部故障':'失败',className:'failure'};
  return {text:state||'未知',className:'failure'};
}
function activityTime(value){const date=value?new Date(value):null;return date&&Number.isFinite(date.getTime())?date.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit'}):'--:--:--'}
function combinedActivity(){
  const tools=toolActivity.map(item=>({...item,kind:item.category||'tool'}));
  const autonomy=autonomyActivity.map((item,index)=>({call_id:'autonomy:'+(item.started_at||index),tool:'自主活动',path:item.result_reason||item.decision||'Peter 完成了一次自主活动',state:item.result==='失败'?'失败':'成功',detail:activityDetails(item),started_at:item.started_at,finished_at:item.finished_at,source:'自主活动',kind:'autonomy'}));
  const glances=glanceActivity.map((item,index)=>({call_id:'glance:'+(item.started_at||index),tool:'瞄一眼',path:(item.foreground?.application||'未识别窗口')+' · '+(item.foreground?.title||item.result_reason||''),state:item.result==='完成'?'成功':'失败',detail:['看到：'+(item.foreground?.application||'未识别窗口')+' · '+(item.foreground?.title||'窗口标题不可用'),item.decision||item.result_reason,'本次费用：¥'+item.cost_cny].filter(Boolean).join('\n'),started_at:item.started_at,finished_at:item.finished_at,source:'瞄一眼',kind:'glance'}));
  return [...tools,...autonomy,...glances].sort((a,b)=>String(b.started_at||b.finished_at||'').localeCompare(String(a.started_at||a.finished_at||'')));
}
function createActivityRow(item,key){
  const details=document.createElement('details');details.className='tool-activity-row';details.dataset.activityCallId=key;details.open=item.state==='进行中';
  const summary=document.createElement('summary'),head=document.createElement('div');head.className='activity-record-head';
  const title=document.createElement('div'),time=document.createElement('time'),name=document.createElement('strong'),status=document.createElement('span');
  title.append(time,name);head.append(title,status);
  const meta=document.createElement('div');meta.className='activity-record-meta';
  const code=document.createElement('code'),source=document.createElement('span');meta.append(code,source);summary.append(head,meta);
  const body=document.createElement('div');body.className='activity-detail';const dl=document.createElement('dl');
  const values={};
  for(const label of ['操作对象','开始时间','完成时间','执行结果']){
    const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;
    if(label==='执行结果'){values.result=document.createElement('pre');values.resultContainer=dd;dd.appendChild(values.result)}else values[label]=dd;
    dl.append(dt,dd);
  }
  body.appendChild(dl);details.append(summary,body);
  return {details,time,name,status,code,source,values};
}
function activityDate(value){
  if(!value)return '';
  const date=new Date(value);
  return Number.isFinite(date.getTime())?date.toLocaleString():String(value);
}
function appendNowhereField(parent,label,value){
  if(value===null||value===undefined||value==='')return;
  const line=document.createElement('p');line.className='nowhere-result-meta';
  const name=document.createElement('span');name.textContent=label+'：';line.append(name,document.createTextNode(String(value)));parent.appendChild(line);
}
function renderNowhereResult(item,parsed){
  const card=document.createElement('div');card.className='nowhere-readable';
  const heading=document.createElement('strong');
  if(parsed.kind==='say'){
    heading.textContent='Peter 留下一段语录';card.appendChild(heading);
    if(parsed.text!==null){const quote=document.createElement('blockquote');quote.className='nowhere-quote';quote.textContent=parsed.text;card.appendChild(quote)}
    else{const note=document.createElement('p');note.className='nowhere-result-meta';note.textContent='语录已记下';card.appendChild(note)}
    if(parsed.truncated)appendNowhereField(card,'说明','语录按长度上限截短');
  }else if(parsed.kind==='quotes'){
    heading.textContent=`本旅程语录（${parsed.returnedCount} 条）`;card.appendChild(heading);
    if(!parsed.quotes.length){const empty=document.createElement('p');empty.className='nowhere-result-meta';empty.textContent=parsed.returnedCount?'没有可展示的原文':'这次没有语录';card.appendChild(empty)}
    for(const quoteData of parsed.quotes){
      const entry=document.createElement('article');entry.className='nowhere-quote-entry';
      const quote=document.createElement('blockquote');quote.className='nowhere-quote';quote.textContent=quoteData.text;entry.appendChild(quote);
      const meta=document.createElement('div');meta.className='nowhere-result-meta-list';
      if(quoteData.place)appendNowhereField(meta,'地点',quoteData.place);
      if(quoteData.sim_time)appendNowhereField(meta,'旅程时间',activityDate(quoteData.sim_time));
      if(Array.isArray(quoteData.pos)&&quoteData.pos.length>=2)appendNowhereField(meta,'坐标',quoteData.pos.slice(0,2).join('，'));
      entry.appendChild(meta);card.appendChild(entry);
    }
    if(parsed.duplicateCount){const note=document.createElement('p');note.className='nowhere-result-meta';note.textContent=`其中 ${parsed.duplicateCount} 条重复`;card.appendChild(note)}
  }else{
    heading.textContent='埋藏纪念品';card.appendChild(heading);
    if(parsed.name)appendNowhereField(card,'纪念品',parsed.name);
    if(parsed.pos&&parsed.pos.length>=2)appendNowhereField(card,'地点',parsed.pos.slice(0,2).join('，'));
    if(parsed.source)appendNowhereField(card,'来源',parsed.source);
    if(parsed.buried_at)appendNowhereField(card,'埋藏时间',activityDate(parsed.buried_at));
    if(parsed.description)appendNowhereField(card,'纪念品描述',parsed.description);
    if(parsed.note){const note=document.createElement('blockquote');note.className='nowhere-quote nowhere-note';note.textContent=parsed.note;card.appendChild(note)}
  }
  const raw=document.createElement('details');raw.className='nowhere-raw-result';
  const summary=document.createElement('summary');summary.textContent='查看原始结果';
  const pre=document.createElement('pre');pre.textContent=String(item.detail||'');raw.append(summary,pre);card.appendChild(raw);
  return card;
}
function updateActivityRow(row,item){
  row.time.textContent=activityTime(item.started_at||item.finished_at);
  const nowhereKind=window.VeilNowhereActivity?.toolKind(item.canonical_tool||item.tool);
  row.name.textContent=nowhereKind?NOWHERE_TOOL_LABELS[nowhereKind]:(TOOL_LABELS[item.tool]||item.tool);
  const screenPopOpen=['还开着','弹幕播放中'].includes(item.screen_pop_state);
  const state=item.screen_pop_state
    ? {text:item.screen_pop_state,className:screenPopOpen?'running':'success'}
    : activityStatus(item.state,item.detail);
  row.status.className='activity-state '+state.className;row.status.textContent=state.text;
  row.code.textContent=nowhereKind?'乌有乡':item.tool;row.source.textContent='来源：'+(item.source||'聊天');
  row.values['操作对象'].textContent=nowhereKind?'乌有乡旅行记录':(item.path||'未提供');
  row.values['开始时间'].textContent=item.started_at?new Date(item.started_at).toLocaleString():'旧记录没有开始时间';
  row.values['完成时间'].textContent=item.finished_at?new Date(item.finished_at).toLocaleString():item.state==='进行中'?'尚未完成':'旧记录没有完成时间';
  const resultNote=item.result_state==='已截断'?'\n结果已截断':
    item.result_state==='结果过长'?'\n结果过长，没给模型':'';
  const resultDetail=String(item.detail||state.text);
  const friendly=window.VeilNowhereActivity?.parse(item,toolActivity);
  if(friendly){
    // 内容没变就复用现有 DOM，避免刷新时重建 <pre> 把滚动位置弹回顶部
    const friendlyKey=String(item.detail||'')+'\u0000'+JSON.stringify(friendly);
    if(row.friendlyKey!==friendlyKey||!row.values.resultContainer.querySelector('.nowhere-readable')){
      const oldRaw=row.values.resultContainer.querySelector('.nowhere-raw-result');
      const rawWasOpen=oldRaw?.open===true,rawTop=oldRaw?.querySelector('pre')?.scrollTop||0;
      const rendered=renderNowhereResult(item,friendly);
      const raw=rendered.querySelector('.nowhere-raw-result');if(raw)raw.open=rawWasOpen;
      row.values.resultContainer.replaceChildren(rendered);
      const newPre=raw?.querySelector('pre');if(newPre&&rawTop)newPre.scrollTop=rawTop;
      row.friendlyKey=friendlyKey;
    }
  }
  else{
    row.friendlyKey=null;
    // 刷新时内容没变就不要动 DOM：重新挂载或重写 textContent 会把「执行结果」框的滚动位置弹回顶部
    const container=row.values.resultContainer,pre=row.values.result;
    if(container.firstElementChild!==pre||container.childNodes.length!==1)container.replaceChildren(pre);
    const nextText=resultDetail+(resultNote&& !resultDetail.includes('结果已截断')?resultNote:'');
    if(pre.textContent!==nextText){const top=pre.scrollTop;pre.textContent=nextText;pre.scrollTop=top}
  }
}
function renderActivityTimeline(){
  const all=combinedActivity();
  const visible=all.filter(item=>activityFilter==='all'||item.kind===activityFilter||(activityFilter==='autonomy'&&item.source==='自主活动'));
  const list=$('activity-timeline');$('activity-count').textContent=all.length+' 条记录';
  const running=toolActivity.filter(item=>item.state==='进行中').length;
  $('activity-work-status').textContent=running?`${running} 项工具执行中`:'等待工具调用';
  $('activity-work-dot').className='status-dot '+(running?'is-busy':'is-online');
  const present=new Set(all.map(item=>String(item.call_id||`${item.tool}:${item.started_at||item.finished_at||''}`)));
  for(const [key,row] of activityRowNodes){if(!present.has(key)){row.details.remove();activityRowNodes.delete(key)}}
  const visibleKeys=new Set(visible.map(item=>String(item.call_id||`${item.tool}:${item.started_at||item.finished_at||''}`)));
  for(const node of [...list.children])if(!visibleKeys.has(node.dataset.activityCallId||''))node.remove();
  let empty=list.querySelector('[data-activity-empty]');
  if(!visible.length){
    if(!empty){empty=document.createElement('p');empty.dataset.activityEmpty='true';empty.className='muted';list.appendChild(empty)}
    empty.textContent=all.length?'没有记录':'还没有记录';return;
  }
  empty?.remove();
  let cursor=list.firstElementChild;
  for(const item of visible){
    const key=String(item.call_id||`${item.tool}:${item.started_at||item.finished_at||''}`);
    let row=activityRowNodes.get(key);
    if(!row){row=createActivityRow(item,key);activityRowNodes.set(key,row)}
    updateActivityRow(row,item);
    if(row.details===cursor)cursor=cursor.nextElementSibling;
    else list.insertBefore(row.details,cursor);
  }
}
function renderActivity(record,history=[],tools=[],glances=[]){
  toolActivity=Array.isArray(tools)?tools:[];autonomyActivity=Array.isArray(history)?history:[];glanceActivity=Array.isArray(glances)?glances:[];renderActivityTimeline();
}
const CONTEXT_KINDS={chat:'聊天',qq:'QQ',autonomy:'自主活动',glance:'瞄一眼',codex:'Codex 提醒'};
const NOTE_STATUS={'进行中':['进行中','active'],'已完成':['已完成','done'],'已过期':['已过期','expired']};
let liveContextKey='';
function shortTime(value){const d=value?new Date(value):null;return d&&Number.isFinite(d.getTime())?d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}):''}
function contextRow(parts){
  const row=document.createElement('div');row.className='context-row';
  const head=document.createElement('div');head.className='head';
  for(const [tag,cls,text] of parts.head){const el=document.createElement(tag);if(cls)el.className=cls;el.textContent=text;head.appendChild(el)}
  row.appendChild(head);
  for(const [cls,text] of parts.lines){if(!text)continue;const el=document.createElement('div');el.className=cls;el.textContent=text;row.appendChild(el)}
  return row;
}
const TOOL_GROUP_LABELS={web:'上网',github:'GitHub',desktop_control:'操控桌面',memory_admin:'整理记忆',build:'干活',desktop_files:'桌面文件',garden_forum:'花园论坛',garden_play:'花园游戏',garden_more:'花园其他',nowhere:'乌有乡',xiaohongshu:'小红书',social:'社交',other:'未分组的新工具'};
function kb(bytes){return (bytes/1024).toFixed(1)+'KB'}
function toolGroupLabel(group){return group?(TOOL_GROUP_LABELS[group]||group.replace(/^mcp_/,'服务 ')):'一直带着'}
function renderToolInventory(inv){
  // Ticket 357: every tool, its size, whether each request carries it, and how often it was used lately.
  const list=$('context-tools'),summary=$('context-tools-summary');if(!list||!summary)return;
  list.replaceChildren();
  const rows=inv&&Array.isArray(inv.rows)?inv.rows:[];
  if(!rows.length){summary.textContent='';const p=document.createElement('p');p.className='muted';p.textContent='还没有记录。';list.appendChild(p);return}
  const budget=inv.budget||{},over=Array.isArray(inv.over)?inv.over:[];
  summary.textContent=`一直带着 ${kb(inv.always_on_bytes||0)}`+(budget.always_on?` / 上限 ${kb(budget.always_on)}`:'')+` · 调用次数取最近 ${inv.recent_call_total||0} 次`;
  for(const item of over){
    const text=item.kind==='always_on'?`一直带着的工具超出上限：${kb(item.bytes)} / ${kb(item.limit)}`:`「${toolGroupLabel(item.group)}」这一组超出上限：${kb(item.bytes)} / ${kb(item.limit)}`;
    list.appendChild(contextRow({head:[['span','note-status active','超出预算']],lines:[['meta',text]]}));
  }
  const groups=new Map();for(const row of rows){const key=row.group||'';if(!groups.has(key))groups.set(key,[]);groups.get(key).push(row)}
  for(const [group,items] of groups){
    const total=items.reduce((sum,row)=>sum+(row.bytes||0),0);
    list.appendChild(contextRow({head:[['strong','',toolGroupLabel(group)],['time','',`${items.length} 个 · ${kb(total)}`+(group?' · 要用时展开':'')]],
      lines:[['meta',items.map(row=>`${row.name} ${kb(row.bytes||0)}`+(row.recent_calls?` ×${row.recent_calls}`:'')).join('、')]]}));
  }
}
let voiceStyleEnabled=false,voiceStyleSaved=null;
function renderVoiceStyle(v){
  // Ticket 355: Lan's rules and sample exchanges for how he talks; never overwrite text she is editing.
  const block=$('voice-style-block');if(!block)return;
  if(!v||v.available!==true){block.hidden=true;return}
  block.hidden=false;voiceStyleEnabled=v.enabled===true;
  $('voice-style-summary').textContent=(voiceStyleEnabled?'开着':'已关闭')+` · ${v.samples||0} 段样例 · ${v.characters||0} / ${v.limit||0} 字`;
  $('voice-style-toggle').textContent=voiceStyleEnabled?'关闭':'开启';
  if(detachedView){$('voice-style-toggle').hidden=$('voice-style-save').hidden=true;$('voice-style-text').readOnly=true} // a detached context window only reads
  const box=$('voice-style-text'),text=typeof v.text==='string'?v.text:'';
  if(voiceStyleSaved===null||box.value===voiceStyleSaved)box.value=text;
  voiceStyleSaved=text;
}
async function postVoiceStyle(body,done){
  const note=$('voice-style-note');note.textContent='';
  try{const r=await api('/api/voice-style',body);const d=await r.json();
    if(!r.ok)throw new Error(d.error||'没保存成功');
    if(body.text!==undefined&&$('voice-style-text').value===body.text)voiceStyleSaved=null;
    renderVoiceStyle(d.voice_style);note.textContent=done}
  catch(e){note.textContent=e.message||'没保存成功'}
}
if($('voice-style-toggle')){
  $('voice-style-toggle').onclick=()=>postVoiceStyle({enabled:!voiceStyleEnabled},voiceStyleEnabled?'已关，下一句不再带':'已开启。');
  $('voice-style-save').onclick=()=>postVoiceStyle({text:$('voice-style-text').value},'已保存，旧版本留在 persona/versions 里。');
}
let workCheckpointClosing=false;
function renderWorkCheckpoint(cp){
  const block=$('work-checkpoint-block'),list=$('work-checkpoint'),summary=$('work-checkpoint-summary'),close=$('work-checkpoint-close');
  if(!block)return;
  if(!cp){block.hidden=true;return}
  block.hidden=false;
  const state={open:'进行中',closed:'已结束',expired:'已失效'}[cp.status]||cp.status||'';
  summary.textContent=state+(cp.status==='open'?'：结束后不再带入':'');
  list.replaceChildren();
  const rows=(items,label)=>Array.isArray(items)&&items.length?label+'：'+items.slice(0,4).map(r=>`${r.tool||'?'} ${r.path||''}${r.state?'（'+r.state+'）':''}`).join('；')+(items.length>4?`；另有 ${items.length-4} 条`:''):'';
  const lines=[
    ['meta',cp.goal?'目标：'+cp.goal:'目标：待确认'],
    ['meta',rows(cp.confirmed,'已确认')],
    ['meta',rows(cp.unconfirmed,'未确认（先核实）')],
    ['meta',rows(cp.failed,'失败或被拒绝')],
    ['meta','下一步：'+(cp.next_step||'待确认')],
    ['meta','记录于 '+(shortTime(cp.updated_at)||'未知')+(cp.closed_at?'，结束于 '+shortTime(cp.closed_at):'')]
  ];
  list.appendChild(contextRow({head:[['strong','','工作断点'],['meta','',cp.goal?'':'目标待确认']],lines}));
  if(close){
    close.hidden=cp.status!=='open'||!!detachedView||workCheckpointClosing;
    close.textContent=workCheckpointClosing?'正在结束…':'结束这个断点';
  }
}
if($('work-checkpoint-close')){
  $('work-checkpoint-close').onclick=async()=>{
    if(workCheckpointClosing)return;
    if(!confirm('结束这个断点？'))return;
    workCheckpointClosing=true;renderWorkCheckpoint(liveCheckpoint);
    try{const r=await api('/api/work-checkpoint',{action:'close',id:liveCheckpoint&&liveCheckpoint.id});const d=await r.json();
      if(!r.ok)throw new Error(d.error||'没有结束成功');
      liveCheckpoint=d.work_checkpoint||liveCheckpoint;
    }catch(e){alert(e.message||'没有结束成功')}
    finally{workCheckpointClosing=false;if(liveCheckpoint)renderWorkCheckpoint(liveCheckpoint)}
  };
}
function recallDetails(r){
  const n=v=>Array.isArray(v)?v.length:0;
  const ids=v=>Array.isArray(v)?v.join(', '):'';
  const lines=[];
  if(r.query_chars)lines.push(`这句 ${r.query_chars} 字符（hash ${r.query_hash||'?'}）`);
  if(n(r.ombre))lines.push(`Ombre 返回：${ids(r.ombre)}`);
  if(n(r.hidden))lines.push('不可见：'+r.hidden.map(x=>`${x.id}（${x.reason||'?'}）`).join('；'));
  if(n(r.vector))lines.push(`向量候选：${ids(r.vector)}`);
  const j=r.judge||{};
  if(j.candidates!==undefined)lines.push(`判官候选 ${j.candidates} 条，选中：${ids(j.picked)||'无'}`);
  if(n(r.dropped))lines.push('去重/冷却：'+r.dropped.map(x=>`${x.id}（${x.reason||'?'}）`).join('；'));
  if(n(r.injected))lines.push(`注入：${ids(r.injected)}`);
  if(n(r.opening))lines.push(`开场：${ids(r.opening)}`);
  if(n(r.budget_dropped))lines.push(`字数裁掉：${ids(r.budget_dropped)}`);
  if(r.failure)lines.push('检索故障：'+r.failure);
  return lines.join('\n')||'这轮没有诊断';
}
function recallText(r){
  if(!r)return '';
  const n=v=>Array.isArray(v)?v.length:0;
  const parts=[];
  parts.push(`Ombre ${n(r.ombre)} 条`);
  if(n(r.hidden)){
    const reasons=[...new Set((r.hidden||[]).map(x=>x&&x.reason).filter(Boolean))];
    parts.push(`不可见 ${n(r.hidden)} 条${reasons.length?'（'+reasons.join('/')+'）':''}`);
  }
  const j=r.judge||{};
  if(j.status==='failed')parts.push('判官失败，用普通检索');
  else if(j.status==='empty')parts.push('判官没找到相关的');
  else if(j.status==='chosen')parts.push(`判官选 ${n(j.picked)} 条`);
  else parts.push('没用判官');
  parts.push(`注入 ${n(r.injected)} 条`);
  if(n(r.dropped))parts.push(`去重/冷却 ${n(r.dropped)} 条`);
  if(n(r.budget_dropped))parts.push(`字数裁掉 ${n(r.budget_dropped)} 条`);
  if(r.failure)parts.push('检索故障：'+r.failure);
  return '记忆：'+parts.join('；');
}
let liveCheckpoint=null;
function renderLiveContext(live){
  live=live||{};liveCheckpoint=live.work_checkpoint||null;renderWorkCheckpoint(liveCheckpoint);const key=JSON.stringify(live);if(key===liveContextKey)return;liveContextKey=key;
  const turns=Array.isArray(live.turns)?live.turns:[],turnList=$('context-turns');turnList.replaceChildren();
  if(!turns.length){const p=document.createElement('p');p.className='muted';p.textContent='还没有记录';turnList.appendChild(p)}
  for(const turn of turns){
    const tools=Array.isArray(turn.tools)?turn.tools:[];
    const counts={};for(const item of tools){const name=item.tool||'?';counts[name]=(counts[name]||0)+1}
    const toolText=Object.entries(counts).map(([name,count])=>count>1?`${name} ×${count}`:name).join('、');
    const row = contextRow({head:[['time','',shortTime(turn.time)],['strong','',CONTEXT_KINDS[turn.kind]||turn.kind||'请求']],
      lines:[[ 'meta',(turn.history_dropped_rounds>0?`原文 ${turn.history_rounds??0} 轮（共 ${turn.history_total_rounds??'?'} 轮，未带 ${turn.history_dropped_rounds} 轮${turn.history_dropped_since?'，最早 '+shortTime(turn.history_dropped_since):''}）`:`原文 ${turn.history_rounds??0} 轮`)+` · 背景 ${turn.background_count||0} 段 · 长期记忆 ${turn.memory_count||0} 条 · 便签 ${turn.notes_count||0} 条`+(turn.estimated_input_tokens?` · 约 ${turn.estimated_input_tokens} tokens`:'')],
        ['meta',turn.trim_note||''],
        ['meta',recallText(turn.recall)],
        ['meta',toolText?'工具：'+toolText:'']]});
    if(turn.recall){
      const details=document.createElement('details');
      const summary=document.createElement('summary');summary.className='meta';summary.textContent='候选明细';
      const body=document.createElement('div');body.className='meta';body.style.whiteSpace='pre-wrap';body.textContent=recallDetails(turn.recall);
      details.append(summary,body);row.appendChild(details);
    }
    turnList.appendChild(row);
  }
  renderToolInventory(live.tool_inventory);
  renderVoiceStyle(live.voice_style);
  const notes=live.notes,noteList=$('context-notes');noteList.replaceChildren();
  if(!notes){$('context-notes-next').textContent='';const p=document.createElement('p');p.className='muted';p.textContent='短期便签没开';noteList.appendChild(p);return}
  const next=shortTime(notes.next_extraction_at);$('context-notes-next').textContent=next?`下次整理便签 ${next}`:'';
  const items=[...(notes.active||[]),...(notes.recent||[])];
  if(!items.length){const p=document.createElement('p');p.className='muted';p.textContent='还没有便签';noteList.appendChild(p)}
  for(const note of items){
    const [label,cls]=NOTE_STATUS[note.status]||[note.status||'',''];
    const fades=note.status==='进行中'&&shortTime(note.fades_at)?`约 ${shortTime(note.fades_at)} 无进展淡出`:'';
    const changed=note.status!=='进行中'&&shortTime(note.changed_at)?`${label}于 ${shortTime(note.changed_at)}`:'';
    noteList.appendChild(contextRow({head:[['time','',shortTime(note.created_at)],['span','note-status '+cls,label]],
      lines:[['note-text',note.text||''],['meta',[note.updated_at&&note.updated_at!==note.created_at?`更新 ${shortTime(note.updated_at)}`:'',fades||changed].filter(Boolean).join(' · ')]]}));
  }
}

async function loadPendingApprovals(force=false){
  if(!force&&Date.now()-pendingApprovalAt<10000)return;
  pendingApprovalAt=Date.now();
  try{
    const r=await api('/api/pending-approvals');
    if(!r.ok)return;
    const d=await r.json();
    pendingApprovalItems=Array.isArray(d.items)?d.items:[];
    renderPendingApprovals();
    syncDesktopRail();
  }catch(e){}
}
function renderPendingApprovals(){
  const block=$('pending-approvals-block'),list=$('pending-approvals-list');
  if(!block||!list)return;
  const pending=pendingApprovalItems.filter(x=>x.status==='pending');
  block.hidden=!pending.length;
  $('pending-approvals-count').textContent=pending.length?pending.length+' 件待处理':'';
  list.replaceChildren();
  for(const item of pending){
    const row=document.createElement('article');row.className='pending-approval';
    const text=document.createElement('p');text.textContent=item.prompt;row.appendChild(text);
    const meta=document.createElement('div');meta.className='pending-meta';
    meta.textContent='请求于 '+String(item.created_at||'').replace('T',' ').slice(0,16)+' · 过期 '+String(item.expires_at||'').replace('T',' ').slice(0,16);
    row.appendChild(meta);
    const actions=document.createElement('div');actions.className='tool-row';
    const yes=document.createElement('button');yes.type='button';yes.className='yes';yes.textContent='同意一次';
    const no=document.createElement('button');no.type='button';no.textContent='拒绝';
    yes.onclick=()=>decidePending(item.id,'approved',yes,no);
    no.onclick=()=>decidePending(item.id,'denied',yes,no);
    actions.append(yes,no);row.appendChild(actions);list.appendChild(row);
  }
}
async function decidePending(id,decision,yes,no){
  yes.disabled=no.disabled=true;
  try{
    const r=await api('/api/pending-approvals',{id,decision});
    const d=await r.json();
    if(!r.ok)throw new Error(d.error||'处理失败');
    notify(decision==='approved'?'已同意，下一次醒来他会看到。':'已拒绝。','success');
    await loadPendingApprovals(true);
  }catch(e){
    notify(e.message||'处理失败','error');
    yes.disabled=no.disabled=false;
  }
}
async function poll(){
  let pollPhase='fetch';
  try{
    const r=await api('/api/events?after='+after);
    if(!r.ok)throw new Error(r.status);
    const s=await r.json();
    pollPhase='render';
    pollFailures=0;
    $('offline').style.display='none';
    if(detachedView==='mcp'){
      $('ended').style.display=s.stopped?'block':'none';
      if(s.events?.length)after=s.events[s.events.length-1].id;
      setTimeout(poll,600);return;
    }
    stopping=s.stopping===true&&!s.stopped;stopped=s.stopped===true;
    $('ended').style.display=stopped?'block':'none';
    $('service-status').textContent=stopped?'服务：已停止':stopping?'服务：正在退出':'服务：运行中';
    $('service-status-detail').textContent='服务运行状态：'+(stopped?'已停止':stopping?'已收到，保存并关闭中':'运行中');
    const runtime=s.runtime_status||{};
    $('runtime-status').textContent=runtime.message||$('service-status').textContent;
    $('runtime-details').hidden=!runtime.state||runtime.state==='healthy';
    $('runtime-detail-text').textContent=runtime.state&&runtime.state!=='healthy'
      ?[runtime.time,runtime.module+' · '+runtime.classification+' · '+runtime.code,runtime.action].filter(Boolean).join('\n')
      :'';
    activityEnabled=s.activity_enabled===true;
    const nextDate=s.next_wake_at?new Date(s.next_wake_at):null;
    const next=nextDate&&Number.isFinite(nextDate.getTime())?nextDate.toLocaleString():'';
    $('next-wake').textContent=next||s.next_wake_reason||'没有确定的唤醒时间';
    $('next-wake-reason').textContent=next?'下次自主活动时间':'';
    const state=!activityEnabled?'已暂停':(s.autonomy_busy?'正在活动':'已开启');
    $('header-autonomy-status').textContent=state;
    $('autonomy-indicator').className='status-dot '+(!activityEnabled?'is-offline':s.autonomy_busy?'is-busy':'is-online');
    $('header-next-wake').textContent=nextDate&&Number.isFinite(nextDate.getTime())
      ?nextDate.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}):'未确定';
    $('header-next-wake').parentElement.title=next||s.next_wake_reason||'没有确定的唤醒时间';
    $('activity-state').textContent=state;
    $('activity-status').textContent='自主活动'+state;
    renderActivity(s.last_activity,s.activity_history||[],s.tool_activity||[],s.glance_activity||[]);
    void loadPendingApprovals();
    renderLiveContext(s.live_context);
    renderModels(s.models);
    $('activity-toggle').textContent=activityEnabled?'暂停自主活动':'开启自主活动';
    $('activity-toggle').disabled=stopping||stopped||exiting;
    $('wakes-today').textContent=Number.isInteger(s.wakes_today)&&Number.isInteger(s.daily_max_runs)?s.wakes_today+' / '+s.daily_max_runs+' 次':'未提供';
    quietEnabled=s.quiet_enabled===true;
    $('quiet-status').textContent=!s.quiet_hours?'未配置':(quietEnabled?'已开启（'+s.quiet_hours+'）':'已关闭（'+s.quiet_hours+' 也会醒）');
    $('quiet-toggle').textContent=quietEnabled?'关闭夜间安静':'开启夜间安静';
    $('quiet-toggle').disabled=!s.quiet_hours||stopping||stopped||exiting;
    const glanceEnabled=s.glance_enabled===true;
    $('glance-status').textContent=!glanceEnabled?'已暂停':!activityEnabled?'全局自主活动暂停中':s.autonomy_busy?'正在瞄一眼':'已开启（15–40 分钟）';
    const glanceNext=s.glance_next_at?new Date(s.glance_next_at):null;
    $('glance-next').textContent=glanceNext&&Number.isFinite(glanceNext.getTime())?glanceNext.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}):(!glanceEnabled?'未排定':!activityEnabled?'恢复后重新计时':'等待电脑前活动');
    $('glance-toggle').textContent=glanceEnabled?'暂停瞄一眼':'开启瞄一眼';
    $('glance-toggle').disabled=stopping||stopped||exiting;
    codexWatchEnabled=s.codex_watch_enabled===true;
    $('codex-watch-status').textContent=!codexWatchEnabled?'已暂停':
      (s.codex_watch_available==='没找到 Codex 会话文件'?s.codex_watch_available:(s.codex_watch_status||'正在同步'));
    $('codex-watch-toggle').textContent=codexWatchEnabled?'暂停监视':'开启监视';
    $('codex-watch-toggle').disabled=stopping||stopped||exiting;
    $('exit-service').disabled=stopping||stopped||exiting;
    document.title=s.name;$('title').textContent=s.name;$('header-model-name').textContent=s.name;
    const capabilityText=s.capability_record||'No capability updates recorded yet.';
    if($('capability-record-text').textContent!==capabilityText)$('capability-record-text').textContent=capabilityText;
    const currentCapabilities=s.current_capabilities||'Current capabilities are not available.';
    if($('current-capabilities-text').textContent!==currentCapabilities)$('current-capabilities-text').textContent=currentCapabilities;
    const memoryPanel=$('memory-panel');
    memoryPanel.hidden=!s.memory_panel_url;
    memoryPanel.onclick=()=>window.veilDesktop.openMemory();
    memoryAvailable=s.memory_available===true;
    $('memory-unavailable').hidden=memoryAvailable;
    if(memoryAvailable&&!memoryLoaded&&!memoryLoading)loadMemoryCatalog(true);
    else if(!memoryAvailable&&!memoryLoaded){$('memory-list').textContent='没有 Ombre 记忆数据';$('memory-count').textContent='不可用'}
    const stick=log.scrollHeight-log.scrollTop-log.clientHeight<80;
    const initial=after===0;pendingApproval=s.pending;
    for(const e of s.events){
      // One event that cannot be drawn must not look like a lost connection or stall the cursor.
      try{receiveEvent(e,initial)}catch(err){
        console.error('event render failed',e&&e.id,err);
        try{add('error','有一条消息显示不了（编号 '+(e&&e.id)+'），其余消息照常')}catch(_){}
      }
      after=e.id;
    }
    // Only the approval being asked right now can be clicked; older cards are inert.
    document.querySelectorAll('.approval').forEach(c=>{
      if(Number(c.dataset.id)!==s.pending){
        c.classList.add('done');
        const status=c.querySelector('.status');
        if(status&&!status.textContent)status.textContent='审批已结束';
      }
    });
    transcribingVoice=s.transcribing_voice===true;setBusy(s.busy);
    if(s.events.length&&stick)log.scrollTop=log.scrollHeight;
  }catch(e){
    if(pollPhase==='render')console.error('event update failed',e);
    else{
      pollFailures++;if(pollFailures===1||pollFailures===3)console.warn('event poll failed',pollFailures,e?.name||'Error',e?.message||String(e));
      if(pollFailures>=3){$('offline').style.display='block';$('service-status').textContent='服务：连接中断';$('service-status-detail').textContent='服务：连接中断';$('activity-toggle').disabled=true;$('exit-service').disabled=true}
    }
  }
  setTimeout(poll,600);
}
function clearImage(invalidate=true){
  if(invalidate)imageChoice++;
  if(selectedImage?.url)URL.revokeObjectURL(selectedImage.url);
  selectedImage=null;imageFile.value='';imagePreview.replaceChildren();imagePreview.hidden=true;composer.classList.remove('image-drop-active');
}
async function detectImageType(file){
  if(IMAGE_TYPES.has(file.type))return file.type;
  if(file.type)return null;
  const bytes=new Uint8Array(await file.slice(0,12).arrayBuffer());
  if(bytes.length>=8&&[137,80,78,71,13,10,26,10].every((v,i)=>bytes[i]===v))return'image/png';
  if(bytes.length>=3&&bytes[0]===255&&bytes[1]===216&&bytes[2]===255)return'image/jpeg';
  if(bytes.length>=4&&String.fromCharCode(...bytes.slice(0,4))==='GIF8')return'image/gif';
  if(bytes.length>=12&&String.fromCharCode(...bytes.slice(0,4))==='RIFF'&&String.fromCharCode(...bytes.slice(8,12))==='WEBP')return'image/webp';
  return null;
}
function canvasBlob(canvas,quality){return new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',quality))}
async function prepareImage(file,mime){
  if(file.size>25*1024*1024)throw new Error('source too large');
  if(typeof ImageDecoder==='function'){
    const decoder=new ImageDecoder({data:file.stream(),type:mime});
    try{await decoder.tracks.ready;const track=decoder.tracks.selectedTrack;if(!track||track.codedWidth*track.codedHeight>40_000_000)throw new Error('too many pixels')}
    finally{decoder.close()}
  }
  const bitmap=await createImageBitmap(file);
  const canvas=document.createElement('canvas');
  const context=canvas.getContext('2d',{alpha:false});context.fillStyle='#fff';context.fillRect(0,0,canvas.width,canvas.height);
  let blob=null;
  for(const edge of [2048,1792,1536,1280,1024,896,768]){
    const scale=Math.min(1,edge/Math.max(bitmap.width,bitmap.height));
    canvas.width=Math.max(1,Math.round(bitmap.width*scale));canvas.height=Math.max(1,Math.round(bitmap.height*scale));
    context.fillStyle='#fff';context.fillRect(0,0,canvas.width,canvas.height);
    context.drawImage(bitmap,0,0,canvas.width,canvas.height);
    for(const quality of [.9,.82,.74,.66,.58,.5,.42,.34]){
      blob=await canvasBlob(canvas,quality);
      if(blob&&blob.size<=4.8*1024*1024)break;
    }
    if(blob&&blob.size<=4.8*1024*1024)break;
  }
  bitmap.close();
  if(!blob||blob.size>4.8*1024*1024)throw new Error('encoded image too large');
  const stem=(file.name||'photo').replace(/\.[^.]*$/,'').slice(0,100)||'photo';
  return new File([blob],stem+'.jpg',{type:'image/jpeg'});
}
async function chooseImage(file){
  const choice=++imageChoice;
  if(!file)return;
  imageFile.value='';
  const mime=await detectImageType(file);
  if(!mime){if(choice===imageChoice)add('error','只支持 PNG、JPEG、GIF、WebP');return}
  let prepared;
  try{prepared=await prepareImage(file,mime)}catch(e){if(choice===imageChoice)add('error',e.message==='source too large'?'原图超过 25 MB':'图片读不了，换一张');return}
  if(choice!==imageChoice)return;
  clearImage(false);
  selectedImage={file:prepared,url:URL.createObjectURL(prepared),media:null,mime:prepared.type,choice,key:crypto.randomUUID()};
  const img=document.createElement('img');img.src=selectedImage.url;img.alt='待发送图片';imagePreview.appendChild(img);
  const label=document.createElement('span');label.textContent=file.name||'图片';imagePreview.appendChild(label);
  const cancel=document.createElement('button');cancel.type='button';cancel.textContent='取消';cancel.onclick=clearImage;selectedImage.cancel=cancel;imagePreview.appendChild(cancel);imagePreview.hidden=false;
}
imageButton.onclick=()=>imageFile.click();
imageFile.onchange=()=>{setComposerPanel(null);chooseImage(imageFile.files?.[0])};
['dragenter','dragover'].forEach(type=>composer.addEventListener(type,e=>{e.preventDefault();composer.classList.add('image-drop-active')}));
['dragleave','drop'].forEach(type=>composer.addEventListener(type,e=>{e.preventDefault();if(type==='drop')chooseImage(e.dataTransfer?.files?.[0]);composer.classList.remove('image-drop-active')}));
if(document.addEventListener)document.addEventListener('paste',e=>{
  const file=[...(e.clipboardData?.items||[])].map(item=>item.kind==='file'?item.getAsFile():null).find(Boolean);
  if(file){e.preventDefault();chooseImage(file)}
});
function fileAsDataUrl(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=reject;reader.readAsDataURL(file)})}
function voiceMime(){
  for(const mime of ['audio/webm;codecs=opus','audio/webm','audio/ogg;codecs=opus','audio/mp4'])if(MediaRecorder.isTypeSupported?.(mime))return mime;
  return '';
}
function stopVoiceTracks(){if(voiceStream){voiceStream.getTracks().forEach(track=>track.stop());voiceStream=null}}
function resetVoiceButton(){clearInterval(voiceTimer);voiceTimer=null;voiceHold.dataset.recording='false';voiceHold.setAttribute('aria-pressed','false');voiceHold.setAttribute('aria-label','录制语音消息');voiceHold.title='录制语音消息';voiceHold.innerHTML=window.veilIcon('mic',18);$('voice-record-status').hidden=true;$('voice-record-cancel').hidden=true}
async function startVoice(){
  if(voiceRecorder||voicePermissionPending||voiceSending||busy||sending||stopping||stopped||speechIsActive())return;
  if(!navigator.mediaDevices?.getUserMedia||typeof MediaRecorder==='undefined'){add('error','这个环境不能录音');return}
  voiceCancelled=false;voicePermissionPending=true;voiceChunks=[];setBusy(busy);
  try{
    const stream=await navigator.mediaDevices.getUserMedia({audio:true});voicePermissionPending=false;
    if(voiceCancelled){stream.getTracks().forEach(track=>track.stop());voiceCancelled=false;resetVoiceButton();setBusy(busy);return}
    voiceStream=stream;
    const mime=voiceMime();voiceRecorder=new MediaRecorder(voiceStream,mime?{mimeType:mime}:undefined);voiceStarted=Date.now();
    voiceRecorder.ondataavailable=e=>{if(e.data?.size)voiceChunks.push(e.data)};
    voiceRecorder.onstop=finishVoice;
    voiceRecorder.start(500);voiceHold.dataset.recording='true';voiceHold.setAttribute('aria-pressed','true');voiceHold.setAttribute('aria-label','点一下结束并发送');voiceHold.title='点一下结束并发送';$('voice-record-status').hidden=false;$('voice-record-cancel').hidden=false;$('voice-record-label').textContent='录音中 0:00 · 再点一次发送';setBusy(busy);
    voiceTimer=setInterval(()=>{const seconds=Math.floor((Date.now()-voiceStarted)/1000);$('voice-record-label').textContent='录音中 '+Math.floor(seconds/60)+':'+String(seconds%60).padStart(2,'0')+' · 再点一次发送';if(seconds>=120)stopVoice(false)},250);
  }catch(e){voicePermissionPending=false;stopVoiceTracks();voiceRecorder=null;resetVoiceButton();setBusy(busy);if(!voiceCancelled)add('error','无法使用麦克风，请在设置里允许');voiceCancelled=false}
}
function stopVoice(cancel){
  if(!voiceRecorder){if(voicePermissionPending)voiceCancelled=true;return}voiceCancelled=voiceCancelled||cancel;resetVoiceButton();
  if(voiceRecorder.state!=='inactive')voiceRecorder.stop();
}
async function uploadVoice(blob,mime){
  const dataUrl=await fileAsDataUrl(blob),comma=dataUrl.indexOf(',');
  const r=await api('/api/voice',{mime,name:'voice-'+Date.now(),data:dataUrl.slice(comma+1),send_first:true},15000);
  let result={};try{result=await r.json()}catch(e){}
  if(!r.ok||!result.media)throw new Error(result.error||'录音保存失败');
  return result.media;
}
async function finishVoice(){
  const recorder=voiceRecorder;voiceRecorder=null;stopVoiceTracks();resetVoiceButton();
  if(voiceCancelled){voiceCancelled=false;setBusy(busy);add('notice','录音已取消');return}
  const mime=(recorder.mimeType||voiceChunks[0]?.type||'audio/webm').split(';')[0];
  const blob=new Blob(voiceChunks,{type:mime});voiceChunks=[];
  if(!blob.size){setBusy(busy);add('error','没录到声音，重试');return}
  if(blob.size>10*1024*1024){setBusy(busy);add('error','录音超过 10 MB，未保存');return}
  voiceSending=true;setBusy(busy);voiceHold.title='正在发送…';
  let savedMedia=null;
  try{
    savedMedia=await uploadVoice(blob,mime);
    savedMedia.transcription_pending=true;
    add('user','[voice message]',undefined,new Date().toISOString(),savedMedia);
    try{await sendVoice(savedMedia)}catch(error){if(error.media)Object.assign(savedMedia,error.media);if(!savedMedia.delivery_error)savedMedia.delivery_error='语音已存，未发送';updateVoiceMessage([...col.querySelectorAll('.msg')].find(node=>node.dataset.mediaId===savedMedia.id),savedMedia)}
  }catch(e){
    const id='local-'+crypto.randomUUID(),localUrl=URL.createObjectURL(blob);
    savedMedia={id,filename:'voice.webm',mime,kind:'voice',localUrl,transcription_pending:false,delivery_error:'语音没存上，录音还在'};
    unsentVoiceBlobs.set(id,{blob,mime});add('user','[voice message]',undefined,new Date().toISOString(),savedMedia);
  }
  finally{voiceSending=false;resetVoiceButton();setBusy(busy)}
}
async function sendVoice(media){
  const text='[voice message]',requestId='voice-send-'+media.id;
  const r=await api('/api/send',{text,request_id:requestId,media:{id:media.id}});let result={};try{result=await r.json()}catch(e){}
  if(result.media)Object.assign(media,result.media);
  if(!r.ok){const error=new Error(result.error||'send');error.media=media;throw error}
  media.delivery_error='';
}
async function retryVoice(media,button,wrap){
  button.disabled=true;button.textContent=media.delivery_error?'正在发送…':'正在识别…';
  try{
    const local=unsentVoiceBlobs.get(media.id);
    if(local){
      const localId=media.id,uploaded=await uploadVoice(local.blob,local.mime);Object.assign(media,uploaded);delete media.localUrl;media.transcription_pending=true;unsentVoiceBlobs.delete(localId);
      const message=wrap.closest('.msg');if(message)message.dataset.mediaId=media.id;
      await sendVoice(media);updateVoiceMessage(message,media);return;
    }
    if(media.delivery_error&&media.transcript){await sendVoice(media);updateVoiceMessage(wrap.closest('.msg'),media);return}
    const r=await api('/api/voice/retry',{media_id:media.id},60000);const result=await r.json();
    if(!r.ok)throw new Error('retry');
    Object.assign(media,result.media||{});
    if(result.transcript){Object.assign(media,result.media||{});media.transcript=result.transcript;media.transcription_pending=false;media.delivery_error='';await sendVoice(media);updateVoiceMessage(wrap.closest('.msg'),media)}
    else{Object.assign(media,result.media||{});media.transcription_error=result.transcription_error||'没识别出文字，可重试';media.transcription_pending=false;updateVoiceMessage(wrap.closest('.msg'),media);button.disabled=false}
  }catch(e){if(e.media)Object.assign(media,e.media);else{media.transcription_error=media.transcript?'结果未确认，录音还在':'重试失败，录音还在';media.transcription_pending=false}updateVoiceMessage(wrap.closest('.msg'),media);button.disabled=false}
}
voiceHold.addEventListener('click',()=>{if(voiceRecorder)stopVoice(false);else if(!voicePermissionPending)startVoice()});
$('voice-record-cancel').addEventListener('click',()=>stopVoice(true));
window.addEventListener('keydown',e=>{if(e.key==='Escape'&&(voiceRecorder||voicePermissionPending)){e.preventDefault();stopVoice(true)}});
window.addEventListener('pagehide',()=>{if(voiceRecorder||voicePermissionPending){stopVoice(true);stopVoiceTracks()}});
async function uploadImage(image){
  if(!image)return null;
  if(image.media)return image.media;
  const dataUrl=await fileAsDataUrl(image.file), comma=dataUrl.indexOf(',');
  const r=await api('/api/media',{mime:image.mime,name:image.file.name,data:dataUrl.slice(comma+1)});
  if(!r.ok)throw new Error('image upload failed');
  const result=await r.json();image.media=result.media;return image.media;
}
async function send(text){
  const fromBox=!text;
  text=(text||box.value).trim();
    if((!text&&!selectedImage)||stopping||stopped||sending||voiceSending||speechIsActive())return;
  const attachedImage=selectedImage;
  if(attachedImage&&text.startsWith('/')){add('error','图片和命令不能一起发');return}
  if(!text)text='[图片]';
  const draftText=text,quote=fromBox?pendingQuote:null;
  if(quote)text=`> ${quote.who}：\n`+quote.text.split('\n').map(line=>'> '+line).join('\n')+'\n\n'+text;
  const payloadKey=text+'\0'+(attachedImage?.key||'');
  if(fromBox&&!draftRequests.has(payloadKey)){
    draftRequests.set(payloadKey,crypto.randomUUID());
  }
  const requestId=fromBox?draftRequests.get(payloadKey):crypto.randomUUID();
  if(attachedImage)awaitingImages.set(requestId,attachedImage);
  if(attachedImage?.cancel)attachedImage.cancel.disabled=true;
  sending=true;setBusy(busy);
  try{
    const media=attachedImage?await uploadImage(attachedImage):null;
    const r=await api('/api/send',{text,request_id:requestId,media:media?{id:media.id}:undefined});
    let result={};try{result=await r.json()}catch(e){}
    if(!r.ok){
      awaitingImages.delete(requestId);
      if(attachedImage&&result.error==='invalid media')attachedImage.media=null;
      const errMsg = r.status===409&&result.error==='busy'
        ?'正在处理上一条消息，没发送'
        :(result.error==='invalid media'
          ?'图片读不了，未发送'
          :((result.error==='storage'||result.error==='media_storage')&&result.message
            ?'消息没存上，输入已保留'+result.message
            :'请求没成功，输入已保留'));
      add('error',errMsg,null,null,null,null,requestId);
    }else{
      if(fromBox){
        if(box.value.trim()===draftText){
          box.value='';resizeBox();
          reportTyping(false);
          try{sessionStorage.removeItem(speechDraftKey)}catch(e){}
          speechDraftActive=false;
          if(pendingQuote===quote)clearQuote();
        }
        draftRequests.delete(payloadKey);
      }
      if(attachedImage&&result.duplicate){awaitingImages.delete(requestId);if(selectedImage===attachedImage)clearImage()}
      if(result.duplicate)add('notice','已提交过，没有重复发送');
    }
  }catch(e){
    const errMsg = attachedImage
      ?'网络未确认，图片仍在发送区'
      :'结果未确认，输入已保留';
    add('error',errMsg,null,null,null,null,requestId);
  }
  finally{
    if(attachedImage?.cancel&&selectedImage===attachedImage)attachedImage.cancel.disabled=false;
    sending=false;log.scrollTop=log.scrollHeight
  }
}
$('new-chat').onclick=()=>{
  if(busy||sending||stopping||stopped||revealQueue.length||speechIsActive())return;
  if(confirm('开始新对话？旧聊天仍在本机'))send('/new');
};
$('capability-record').onclick=()=>{$('capability-details').open=!$('capability-details').open};
sendBtn.onclick=()=>send();
box.addEventListener('keydown',e=>{
  if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing&&e.keyCode!==229){e.preventDefault();if(!speechIsActive())send()}
});
box.addEventListener('input',()=>{resizeBox();if(speechDraftActive)saveSpeechDraft(box.value)});
let typingTimer=null,lastTyping=null,lastTypingAt=0;
function reportTyping(value){
  // Ticket 150: keep telling the service while keys are still being pressed, so Peter waits for her next line.
  const now=Date.now();
  if(value===lastTyping&&!(value&&now-lastTypingAt>1000))return;
  lastTyping=value;lastTypingAt=now;api('/api/typing',{typing:value}).catch(()=>{});
}
box.addEventListener('input',()=>{
  if(typingTimer)clearTimeout(typingTimer);
  if(box.value.length>0)reportTyping(true);
  else typingTimer=setTimeout(()=>reportTyping(box.value.length>0),180);
});
$('request-reply').onclick=async()=>{
  if(stopping||stopped)return;
  const r=await api('/api/request-reply',{});
  if(!r.ok)add('error','没提交成功，稍后重试');
};
document.querySelectorAll('[data-cmd]').forEach(b=>b.onclick=()=>send(b.dataset.cmd));
$('activity-toggle').onclick=async()=>{
  $('activity-toggle').disabled=true;
  try{
    const r=await api('/api/activity',{enabled:!activityEnabled});
    if(!r.ok)throw new Error(r.status);
    activityEnabled=!activityEnabled;
    $('activity-status').textContent='自主活动'+(activityEnabled?'已开启':'已暂停');
    $('activity-toggle').textContent=activityEnabled?'暂停自主活动':'开启自主活动';
  }catch(e){$('activity-status').textContent='状态没存上，检查 Agent 连接';add('error','状态没存上，检查连接')}
  finally{$('activity-toggle').disabled=stopping||stopped||exiting}
};
$('quiet-toggle').onclick=async()=>{
  $('quiet-toggle').disabled=true;
  try{
    const enabled=!quietEnabled;
    const r=await api('/api/quiet-hours',{enabled});
    if(!r.ok)throw new Error(r.status);
    quietEnabled=enabled;
    $('quiet-toggle').textContent=quietEnabled?'关闭夜间安静':'开启夜间安静';
  }catch(e){$('quiet-status').textContent='保存失败，检查 Agent 连接';add('error','保存失败，检查连接')}
  finally{$('quiet-toggle').disabled=stopping||stopped||exiting}
};
$('glance-toggle').onclick=async()=>{
  $('glance-toggle').disabled=true;
  try{
    const enabled=$('glance-toggle').textContent==='开启瞄一眼';
    const r=await api('/api/glance',{enabled});
    if(!r.ok)throw new Error(r.status);
  }catch(e){$('glance-status').textContent='保存失败，检查 Agent 连接';add('error','保存失败，检查连接')}
  finally{$('glance-toggle').disabled=stopping||stopped||exiting}
};
$('codex-watch-toggle').onclick=async()=>{
  $('codex-watch-toggle').disabled=true;
  try{
    const enabled=!codexWatchEnabled;
    const r=await api('/api/codex-watch',{enabled});
    if(!r.ok)throw new Error(r.status);
    codexWatchEnabled=enabled;
    $('codex-watch-status').textContent=enabled?'等待 Codex 会话':'已暂停';
    $('codex-watch-toggle').textContent=enabled?'暂停监视':'开启监视';
  }catch(e){$('codex-watch-status').textContent='保存失败，检查 Agent 连接';add('error','保存失败，检查连接')}
  finally{$('codex-watch-toggle').disabled=stopping||stopped||exiting}
};
$('exit-service').onclick=async()=>{
  if(exiting||stopped||!confirm('确定退出 Peter 网页服务吗？'))return;
  exiting=true;$('exit-service').disabled=true;$('activity-toggle').disabled=true;
  $('service-status').textContent='服务：退出中…';
  try{
    const r=await api('/api/exit',{});
    let result={};try{result=await r.json()}catch(e){}
    if(!r.ok||!result.ok)throw new Error(r.status);
    if(result.state==='stopped'){
      stopping=false;stopped=true;
      $('ended').style.display='block';$('service-status').textContent='服务：已停止';
      sendBtn.disabled=true;$('activity-toggle').disabled=true;$('exit-service').disabled=true;
    }else if(result.state==='accepted'||result.state==='stopping'){
      exiting=false;stopping=true;
      $('ended').style.display='none';
      $('service-status').textContent='服务：保存并退出中';
      sendBtn.disabled=true;$('activity-toggle').disabled=true;$('exit-service').disabled=true;
    }else throw new Error('invalid exit response');
  }catch(e){
    exiting=false;
    $('service-status').textContent='服务：退出未确认';
    add('error','没收到退出确认，别重复启动');
  }
};
$('reconnect').onclick=async()=>{
  const button=$('reconnect');button.disabled=true;button.textContent='重连中…';
  try{await window.veilDesktop.reconnect()}finally{button.disabled=false;button.textContent='重新连接'}
};
const APPEARANCE_KEY='veil-desktop-appearance-v1';
const appearanceDefaults={theme:'light',windowOpacity:88,pageOpacity:{chat:88,activity:88,context:88,memory:88,mcp:88,more:88},bubbleOpacity:90,backgroundOpacity:100,petSize:320};
function clampSetting(value,min,max,fallback){const n=Number(value);return Number.isFinite(n)?Math.max(min,Math.min(max,Math.round(n))):fallback}
function loadAppearance(){
  try{
    const saved=JSON.parse(localStorage.getItem(APPEARANCE_KEY)||'{}');
    const pageOpacity={};
    for(const page of ['chat','activity','context','memory','mcp','more'])pageOpacity[page]=clampSetting(saved.pageOpacity?.[page],20,100,clampSetting(saved.windowOpacity,20,100,88));
    return {theme:saved.theme==='dark'?'dark':'light',windowOpacity:clampSetting(saved.windowOpacity,20,100,88),pageOpacity,
      bubbleOpacity:clampSetting(saved.bubbleOpacity,55,100,90),backgroundOpacity:clampSetting(saved.backgroundOpacity,0,100,100),petSize:clampSetting(saved.petSize,160,720,320)};
  }catch{return {...appearanceDefaults}}
}
let appearance=loadAppearance();
function storeAppearance(){try{localStorage.setItem(APPEARANCE_KEY,JSON.stringify(appearance))}catch{}}
function applyAppearance(){
  document.documentElement.dataset.theme=appearance.theme;
  document.documentElement.style.setProperty('--window-alpha',String(appearance.windowOpacity/100));
  document.documentElement.style.setProperty('--bubble-alpha',String(appearance.bubbleOpacity/100));
  document.documentElement.style.setProperty('--background-opacity',String(appearance.backgroundOpacity/100));
  for(const page of ['chat','activity','context','memory','mcp','more']){
    document.querySelector(`[data-page-view="${page}"]`)?.style.setProperty('--view-alpha',String(appearance.pageOpacity[page]/100));
    $(`${page}-opacity`).value=String(appearance.pageOpacity[page]);$(`${page}-opacity-value`).value=appearance.pageOpacity[page]+'%';
  }
  document.querySelectorAll('[data-theme-choice]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.themeChoice===appearance.theme)));
  $('window-opacity').value=String(appearance.windowOpacity);$('window-opacity-value').value=appearance.windowOpacity+'%';
  $('bubble-opacity').value=String(appearance.bubbleOpacity);$('bubble-opacity-value').value=appearance.bubbleOpacity+'%';
  $('background-opacity').value=String(appearance.backgroundOpacity);$('background-opacity-value').value=appearance.backgroundOpacity+'%';
  $('pet-size').value=String(appearance.petSize);$('pet-size-value').value=appearance.petSize+' px';
}
applyAppearance();
window.addEventListener('storage',event=>{
  if(event.key===APPEARANCE_KEY){appearance=loadAppearance();applyAppearance()}
  if(event.key===`${APPEARANCE_KEY}-css`){try{$('custom-css').value=event.newValue||'';applyCustomCSS(event.newValue||'')}catch{}}
});
document.querySelectorAll('[data-theme-choice]').forEach(button=>button.onclick=()=>{
  appearance.theme=button.dataset.themeChoice;applyAppearance();if(document.body.dataset.currentPage==='life')renderLife();storeAppearance();window.veilDesktop.saveTheme(appearance.theme);
});
window.veilDesktop.saveTheme(appearance.theme).catch(()=>{});
function setAllWindowOpacity(value){appearance.windowOpacity=clampSetting(value,20,100,88);for(const page of ['chat','activity','context','memory','mcp','more'])appearance.pageOpacity[page]=appearance.windowOpacity;applyAppearance();storeAppearance()}
$('window-opacity').oninput=()=>setAllWindowOpacity($('window-opacity').value);
for(const page of ['chat','activity','context','memory','mcp','more'])$(`${page}-opacity`).oninput=()=>{appearance.pageOpacity[page]=clampSetting($(`${page}-opacity`).value,20,100,88);applyAppearance();storeAppearance()};
$('bubble-opacity').oninput=()=>{appearance.bubbleOpacity=clampSetting($('bubble-opacity').value,55,100,90);applyAppearance();storeAppearance()};
$('background-opacity').oninput=()=>{appearance.backgroundOpacity=clampSetting($('background-opacity').value,0,100,100);applyAppearance();storeAppearance()};
$('pet-size').oninput=()=>{appearance.petSize=clampSetting($('pet-size').value,160,720,320);$('pet-size-value').value=appearance.petSize+' px';storeAppearance()};
$('pet-size').onchange=()=>window.veilDesktop.setPetSize(appearance.petSize);
window.veilDesktop.petSize().then(size=>{if(Number.isFinite(size)){appearance.petSize=clampSetting(size,160,720,320);applyAppearance();storeAppearance()}}).catch(()=>{});
function setBackground(dataUrl,name){
  document.documentElement.style.setProperty('--user-background',dataUrl?`url("${dataUrl}")`:'none');
  $('background-name').textContent=name||'还没选图片';$('clear-background').disabled=!dataUrl;
}
window.veilDesktop.getBackground().then(dataUrl=>setBackground(dataUrl,dataUrl?'已保存的背景图片':'' )).catch(()=>setBackground(null,''));
$('choose-background').onclick=async()=>{try{const result=await window.veilDesktop.chooseBackground();if(result){setBackground(result.dataUrl,result.name);notify('背景图已保存')}}catch(error){notify(error.message||'背景图读取失败','error')}};
$('clear-background').onclick=async()=>{if(await window.veilDesktop.clearBackground()){setBackground(null,'');notify('背景图已移除')}};
const workbench={state:null,changed:false};
function workbenchNote(text){const note=$('workbench-note');note.textContent=text||'';note.hidden=!text}
function renderWorkbench(){
  const s=workbench.state;if(!s)return;
  $('workbench-on').setAttribute('aria-pressed',String(s.enabled));$('workbench-off').setAttribute('aria-pressed',String(!s.enabled));
  const list=$('workbench-folders');list.textContent='';
  for(const folder of s.project_roots){
    const row=document.createElement('div');row.className='wb-folder';
    const name=document.createElement('span');name.textContent=folder;name.title=folder;
    const remove=document.createElement('button');remove.type='button';remove.textContent='移除';remove.onclick=()=>saveWorkbench({remove:folder});
    row.append(name,remove);list.append(row);
  }
  const hosts=$('workbench-hosts');hosts.textContent='';
  for(const host of s.web_hosts||[]){
    const row=document.createElement('div');row.className='wb-folder';
    const name=document.createElement('span');name.textContent=host;
    const remove=document.createElement('button');remove.type='button';remove.textContent='移除';remove.onclick=()=>saveWorkbench({remove_host:host});
    row.append(name,remove);hosts.append(row);
  }
  workbenchNote(s.enabled&&!s.image_ready?'还没有运行环境，双击 workbench-switch.cmd':workbench.changed?'重启后生效':'');
}
let workbenchBusy=Promise.resolve();
function saveWorkbench(change){workbenchBusy=workbenchBusy.then(()=>saveWorkbenchNow(change));return workbenchBusy}
async function saveWorkbenchNow(change){
  try{
    const r=await api('/api/workbench',change);const data=await r.json();
    if(!r.ok){notify(data.error||'没保存成功','error');return}
    workbench.state=data;workbench.changed=true;renderWorkbench();
  }catch(error){notify(error.message||'没保存成功','error')}
}
async function loadWorkbench(){
  try{const r=await api('/api/workbench');const data=await r.json();if(r.ok){workbench.state=data;renderWorkbench()}else workbenchNote(data.error||'读不到工作台设置，查 workbench.json')}catch(error){workbenchNote('读不到工作台设置。')}
}
$('workbench-host-form').onsubmit=async event=>{event.preventDefault();const input=$('workbench-host-input');const value=input.value.trim();if(!value)return;await saveWorkbench({add_host:value});input.value=''};
$('workbench-on').onclick=()=>saveWorkbench({enabled:true});
$('workbench-off').onclick=()=>saveWorkbench({enabled:false});
$('workbench-add').onclick=async()=>{
  try{const folder=await window.veilDesktop.chooseFolder();if(folder)await saveWorkbench({add:folder})}
  catch(error){notify(error.message||'没选上','error')}
};
workbenchBusy=workbenchBusy.then(loadWorkbench);
async function changeWindowsApp(change){
  try{const r=await api('/api/windows-apps',change);const data=await r.json();if(!r.ok){notify(data.error||'没保存成功','error');return}renderWindowsApps(data)}catch(error){notify(error.message||'没保存成功','error')}
}
function windowsAppRow(app,text,handler){
  const row=document.createElement('div');row.className='wb-folder';
  const name=document.createElement('span');name.textContent=app.label;row.append(name);
  if(text){const button=document.createElement('button');button.type='button';button.textContent=text;button.onclick=handler;row.append(button)}
  return row;
}
function renderWindowsApps(data){
  const list=$('windows-apps-list');list.textContent='';
  for(const app of data.builtin||[])list.append(windowsAppRow(app,'',null));
  for(const app of data.apps||[])list.append(windowsAppRow(app,'撤销',()=>changeWindowsApp({remove:app.app_id})));
  const found=$('windows-apps-candidates');found.textContent='';
  for(const app of data.candidates||[])found.append(windowsAppRow(app,'添加',()=>changeWindowsApp({add:app.app_id})));
}
async function loadWindowsApps(){try{const r=await api('/api/windows-apps');const data=await r.json();if(r.ok)renderWindowsApps(data)}catch(error){}}
loadWindowsApps();
let instructionsData=null,instructionsOpen='',instructionsEditing='',instructionsSaving=false,instructionsQuery='';
function instructionMatches(row,query){
  if(!query)return true;
  const hay=[row.title,row.site,row.note,row.text].map(value=>String(value||'').toLocaleLowerCase());
  return hay.some(value=>value.includes(query));
}
function instructionSnippet(row,query){
  if(!query)return '';
  const text=String(row.text||'').toLocaleLowerCase();
  const at=text.indexOf(query);
  if(at<0)return '';
  const source=String(row.text||'');
  const from=Math.max(0,at-28),to=Math.min(source.length,at+query.length+46);
  return (from>0?'…':'')+source.slice(from,to)+(to<source.length?'…':'');
}
function renderInstructions(data){
  instructionsData=data;
  const list=$('instructions-list');list.replaceChildren();
  const categories=Array.isArray(data.categories)?data.categories:[];
  const query=String(instructionsQuery||'').toLocaleLowerCase();
  let total=0,shown=0,matches=[];
  for(const group of categories){
    const entries=(Array.isArray(group.entries)?group.entries:[]).filter(row=>instructionMatches(row,query));
    total+=Array.isArray(group.entries)?group.entries.length:0;
    if(!entries.length)continue;
    shown+=entries.length;matches=matches.concat(entries);
    const section=document.createElement('section');section.className='instr-section';
    const heading=document.createElement('h3');heading.className='instr-heading';heading.textContent=group.title;
    const count=document.createElement('small');
    count.textContent=query?entries.length+' 条匹配':entries.length+' 条'+(group.editable?' · '+group.editable+' 条可改':'');
    heading.appendChild(count);section.appendChild(heading);
    for(const row of entries)section.appendChild(instructionEntry(row));
    list.appendChild(section);
  }
  if(!shown){const p=document.createElement('p');p.className='instr-meta';p.textContent=query?'没有匹配的指令':'没有登记任何指令';list.appendChild(p)}
  $('instructions-status').textContent=query?shown+' 条匹配 · 共 '+total+' 条':total+' 条 · 更新于 '+shortTime(data.generated_at);
  const maintenance=$('instructions-maintenance');
  const counts=data&&data.maintenance;
  if(maintenance){
    if(!counts){maintenance.hidden=true}
    else{
      maintenance.hidden=false;
      maintenance.textContent='你改过 '+Number(counts.overridden||0)+' 条'
        +(Number(counts.default_changed||0)?('；其中 '+Number(counts.default_changed)+' 条代码里的默认后来更新过，你的版本仍在用'):'')
        +'。';
    }
  }
}
function instructionEntry(row){
  const details=document.createElement('details');details.className='instr-entry';
  const searching=!!instructionsQuery;
  const autoOpen=searching&&(instructionsSnippet(row,instructionsQuery)||'').trim().length>0;
  details.open=instructionsOpen===row.id||autoOpen;
  const summary=document.createElement('summary');
  const line=document.createElement('span');line.className='instr-line';
  const chev=document.createElement('span');chev.className='instr-chev';chev.textContent=details.open?'▾':'▸';
  const title=document.createElement('span');title.className='instr-title';title.textContent=row.title;
  const badge=document.createElement('span');badge.className='instr-badge';
  badge.textContent=(row.kind_label||'')+(row.kind==='text'&&!row.editable?' · 只读':'');
  line.append(chev,title,badge);
  if(row.overridden){const changed=document.createElement('span');changed.className='instr-badge changed';changed.textContent='你改过';line.appendChild(changed)}
  if(row.default_changed){const stale=document.createElement('span');stale.className='instr-badge stale';stale.textContent='默认已更新';line.appendChild(stale)}
  summary.appendChild(line);
  const snippet=searching?instructionSnippet(row,instructionsQuery):'';
  if(snippet){const text=document.createElement('span');text.className='instr-snippet';text.textContent=snippet;summary.appendChild(text)}
  summary.onclick=(event)=>{
    event.preventDefault();
    instructionsOpen=details.open?'':row.id;
    if(instructionsOpen!==row.id)instructionsEditing='';
    renderInstructions(instructionsData);
  };
  details.appendChild(summary);
  if(details.open){
    const body=document.createElement('div');body.className='instr-body';
    const meta=document.createElement('p');meta.className='instr-meta';
    meta.textContent=row.site+(row.note?'　'+row.note:'')
      +(row.default_changed?'　代码里的默认后来更新过，你这一版仍在用。':'');
    body.appendChild(meta);
    const source=document.createElement('p');source.className='instr-source';source.textContent='来源：'+row.source;body.appendChild(source);
    if(row.kind==='text'&&row.editable&&instructionsEditing===row.id){
      body.appendChild(instructionEditor(row));
    }else if(row.kind==='text'&&row.editable){
      const pre=document.createElement('pre');pre.className='instr-text';pre.textContent=row.text||'（空）';body.appendChild(pre);
      const actions=document.createElement('div');actions.className='instr-actions';
      const edit=document.createElement('button');edit.type='button';edit.textContent='改这一条';
      edit.onclick=()=>{instructionsEditing=row.id;instructionsOpen=row.id;renderInstructions(instructionsData)};
      const hint=document.createElement('span');hint.className='instr-hint';
      hint.textContent=row.category==='autonomy'?'保存后，下一次自主活动用新内容':'保存后，下一次请求用新内容';
      actions.append(edit,hint);body.appendChild(actions);
    }else{
      const pre=document.createElement('pre');pre.className='instr-text';
      pre.textContent=row.text||'（这一条每轮由程序生成，没有固定原文；上面写了它在请求里的位置）';
      body.appendChild(pre);
    }
    details.appendChild(body);
  }
  return details;
}
function instructionEditor(row){
  const box=document.createElement('div');box.className='instr-actions';box.style.flexDirection='column';box.style.alignItems='stretch';
  const area=document.createElement('textarea');area.className='instr-editor';area.value=row.text;area.spellcheck=false;
  const error=document.createElement('p');error.className='instr-error';error.hidden=true;
  const row2=document.createElement('div');row2.className='instr-actions';
  const save=document.createElement('button');save.type='button';save.textContent='保存';save.disabled=instructionsSaving;
  const cancel=document.createElement('button');cancel.type='button';cancel.textContent='取消';
  cancel.onclick=()=>{instructionsEditing='';renderInstructions(instructionsData)};
  save.onclick=async()=>{
    if(instructionsSaving)return;
    instructionsSaving=true;save.disabled=true;save.textContent='保存中…';
    try{
      const response=await api('/api/instructions',{id:row.id,text:area.value});
      const data=await response.json();
      if(!response.ok){error.hidden=false;error.textContent='没有保存：'+((data&&data.error)||response.status);return}
      instructionsData=data.page;instructionsEditing='';instructionsOpen=row.id;
      renderInstructions(instructionsData);
      notify(row.category==='autonomy'?'已保存，下一次自主活动用新内容。':'已保存，下一次请求用新内容。');
    }catch(err){error.hidden=false;error.textContent='没有保存：'+err.message}
    finally{instructionsSaving=false;save.textContent='保存';save.disabled=false}
  };
  row2.append(save,cancel);
  box.append(area,error,row2);
  return box;
}
async function loadInstructions(){
  try{
    const response=await api('/api/instructions');
    const data=await response.json();
    if(!response.ok){$('instructions-status').textContent='读取失败：'+((data&&data.error)||response.status);return}
    renderInstructions(data);
  }catch(error){$('instructions-status').textContent='读取失败：'+error.message}
}
let instructionsTab='list',snapshotBundle=null,snapshotStepIndex=-1;
function setInstructionsTab(tab){
  instructionsTab=tab==='snapshots'?'snapshots':'list';
  const list=$('instructions-pane-list'),snaps=$('instructions-pane-snapshots');
  if(list)list.hidden=instructionsTab!=='list';
  if(snaps)snaps.hidden=instructionsTab!=='snapshots';
  const listTab=$('instr-tab-list'),snapTab=$('instr-tab-snapshots');
  if(listTab){listTab.classList.toggle('active',instructionsTab==='list');listTab.setAttribute('aria-selected',String(instructionsTab==='list'))}
  if(snapTab){snapTab.classList.toggle('active',instructionsTab==='snapshots');snapTab.setAttribute('aria-selected',String(instructionsTab==='snapshots'))}
  if(instructionsTab==='snapshots')loadSnapshots();
}
function snapshotKind(kind){return {chat:'聊天',autonomy:'自主活动',glance:'瞄一眼',qq:'QQ',codex:'Codex'}[kind]||kind||'请求'}
async function loadSnapshots(){
  const box=$('snapshot-list');if(!box)return;
  try{
    const response=await api('/api/request-snapshots?limit=12');const data=await response.json();
    if(!response.ok){box.textContent='读取失败：'+((data&&data.error)||response.status);return}
    box.replaceChildren();
    const items=Array.isArray(data.items)?data.items:[];
    if(!items.length){const p=document.createElement('p');p.className='muted';p.textContent='还没有快照；更新后发一句话就会生成。';box.appendChild(p);return}
    for(const item of items){
      const button=document.createElement('button');button.type='button';button.className='snapshot-row';
      const kind=document.createElement('strong');kind.textContent=snapshotKind(item.kind);
      const when=document.createElement('span');when.textContent=shortTime(item.created_at)||item.created_at||'';
      const model=document.createElement('span');model.textContent=item.model||'';
      const extra=[];
      extra.push(Number(item.steps||1)+' 次请求');
      extra.push('约 '+Number(item.estimated_tokens||0)+' token');
      if(item.truncated_blocks)extra.push('截断 '+item.truncated_blocks+' 段');
      if(item.redacted)extra.push('打码 '+item.redacted+' 处');
      const size=document.createElement('span');size.textContent=extra.join(' · ');
      button.append(kind,when,model,size);
      button.onclick=()=>openSnapshot(item.request_id);
      box.appendChild(button);
    }
  }catch(error){box.textContent='读取失败：'+error.message}
}
async function openSnapshot(requestId,messageId){
  const url=messageId?('/api/request-snapshot?message_id='+encodeURIComponent(messageId)):('/api/request-snapshot?id='+encodeURIComponent(requestId||''));
  try{
    const response=await api(url);const data=await response.json();
    if(!response.ok){notify((data&&data.error)||'没有这一轮的快照','error');return false}
    snapshotBundle=data;snapshotStepIndex=(data.steps||[]).length-1;
    renderSnapshot();return true;
  }catch(error){notify('读取快照失败：'+error.message,'error');return false}
}
function renderSnapshot(){
  const box=$('snapshot-detail'),bundle=snapshotBundle;if(!box||!bundle)return;
  box.hidden=false;box.replaceChildren();
  const steps=Array.isArray(bundle.steps)?bundle.steps:[];
  if(!steps.length){const p=document.createElement('p');p.className='muted';p.textContent='这一轮没有记录。';box.appendChild(p);return}
  const index=Math.max(0,Math.min(snapshotStepIndex,steps.length-1));
  const record=steps[index];
  const head=document.createElement('article');head.className='surface snapshot-head';
  const title=document.createElement('h3');title.textContent=snapshotKind(record.kind)+' · '+(shortTime(record.created_at)||record.created_at||'');
  const meta=document.createElement('p');meta.className='muted';
  meta.textContent=(record.model||'')+' · 约 '+Number(record.estimated_tokens||0)+' token · 第 '+(index+1)+'/'+steps.length+' 次请求'+(record.trim_note?'　'+record.trim_note:'');
  head.append(title,meta);
  if((record.memory_ids||[]).length){
    const ids=document.createElement('p');ids.className='muted';
    ids.textContent='自动想起的记忆：'+record.memory_ids.length+' 条（'+record.memory_ids.slice(0,6).join('、')+'）';
    head.appendChild(ids);
  }
  const range=document.createElement('p');range.className='snap-range';
  const cut=Number(record.truncated_blocks||0),hidden=Number(record.redacted||0);
  const tools=Array.isArray(record.tools)?record.tools:[];
  const parts=[];
  parts.push(record.complete?'正文与工具定义都记录了':'有内容被截断或省略'+(cut?'（'+cut+' 段）':''));
  parts.push(hidden?('敏感串打码 '+hidden+' 处（打码处不可还原）'):'没有发现需要打码的敏感串');
  parts.push('工具定义 '+tools.length+' 个 · '+(Math.round(Number(record.tools_bytes||0)/1024*10)/10)+' KB'+(record.tools_complete?'':'（参数过大，已省略）'));
  range.textContent='记录范围：'+parts.join('；');
  head.appendChild(range);
  box.appendChild(head);
  if(steps.length>1){
    const tabs=document.createElement('div');tabs.className='snapshot-steps';
    steps.forEach((step,stepIndex)=>{
      const button=document.createElement('button');button.type='button';
      button.className=stepIndex===index?'active':'';
      const calls=(step.blocks||[]).filter(block=>block.role==='tool').length;
      button.textContent='第 '+(stepIndex+1)+' 次'+(stepIndex>0&&calls?' · '+calls+' 个工具返回':'');
      button.onclick=()=>{snapshotStepIndex=stepIndex;renderSnapshot()};
      tabs.appendChild(button);
    });
    box.appendChild(tabs);
  }
  if(tools.length){
    const toolsBox=document.createElement('details');toolsBox.className='snap-tools';
    const summary=document.createElement('summary');summary.textContent='这一轮发给模型的工具定义 · '+tools.length+' 个';
    toolsBox.appendChild(summary);
    for(const tool of tools){
      const item=document.createElement('details');item.className='snap-tool';
      const line=document.createElement('summary');line.textContent=tool.name||'';
      item.appendChild(line);
      const pre=document.createElement('pre');pre.className='instr-text';
      pre.textContent='说明：'+(tool.description||'（空）')+'\n参数：'+JSON.stringify(tool.parameters||{},null,1);
      item.appendChild(pre);toolsBox.appendChild(item);
    }
    box.appendChild(toolsBox);
  }
  if(index===0&&Array.isArray(record.blocks)&&record.blocks.length){
    // step 1 carries the stable prefix; later steps repeat it, so the marker is shown once
  }
  const blocks=Array.isArray(record.blocks)?record.blocks:[];
  blocks.forEach((block,blockIndex)=>{
    if(blockIndex===Number(record.cache_prefix||0)){
      const line=document.createElement('p');line.className='snap-boundary';
      line.textContent='── 以上是固定前缀（会命中缓存）；以下是这一轮的历史与临时内容 ──';
      box.appendChild(line);
    }
    const details=document.createElement('details');details.className='snap-block';
    const summary=document.createElement('summary');
    const label=document.createElement('strong');label.textContent='#'+(blockIndex+1)+' '+block.title;
    const meta2=document.createElement('span');meta2.className='muted';
    meta2.textContent=' '+block.role+' · '+block.chars+' 字'+(block.label?' · '+block.label:'')+(block.truncated?' · 已截断':'');
    summary.append(label,meta2);details.appendChild(summary);
    const pre=document.createElement('pre');pre.className='instr-text';pre.textContent=block.text||'（空）';details.appendChild(pre);
    if(block.role==='user')details.open=true;
    box.appendChild(details);
  });
}
async function openSnapshotForMessage(messageId){
  setPage('instructions');setInstructionsTab('snapshots');
  await openSnapshot(null,messageId);
}
$('instr-tab-list').onclick=()=>setInstructionsTab('list');
$('instr-tab-snapshots').onclick=()=>setInstructionsTab('snapshots');

$('instructions-search').addEventListener('input',event=>{
  instructionsQuery=String(event.target.value||'').trim().toLocaleLowerCase();
  instructionsOpen='';instructionsEditing='';
  if(instructionsData)renderInstructions(instructionsData);
});
function applyCustomCSS(value){
  const css=String(value||'');
  if(css.length>20000||/@import\b|url\s*\(|expression\s*\(|-moz-binding|behavior\s*:/i.test(css))throw new Error('CSS 不能加载外部资源或脚本');
  $('custom-style').textContent=css;
}
try{$('custom-css').value=localStorage.getItem(`${APPEARANCE_KEY}-css`)||'';applyCustomCSS($('custom-css').value)}catch{$('custom-css').value=''}
$('apply-css').onclick=()=>{try{applyCustomCSS($('custom-css').value);localStorage.setItem(`${APPEARANCE_KEY}-css`,$('custom-css').value);notify('CSS 已保存')}catch(error){notify(error.message,'error')}};
$('reset-appearance').onclick=async()=>{appearance={...appearanceDefaults,pageOpacity:{...appearanceDefaults.pageOpacity}};applyAppearance();storeAppearance();window.veilDesktop.setPetSize(appearance.petSize);$('custom-css').value='';$('custom-style').textContent='';localStorage.removeItem(`${APPEARANCE_KEY}-css`);await window.veilDesktop.clearBackground();setBackground(null,'');notify('已恢复默认')};
$('memory-search').addEventListener('input',()=>{memoryShown=50;renderMemoryList()});
document.querySelectorAll('[data-memory-group]').forEach(button=>button.onclick=()=>{
  memoryGroup=button.dataset.memoryGroup;memoryShown=50;
  document.querySelectorAll('[data-memory-group]').forEach(other=>other.classList.toggle('active',other===button));
  if(MEMORY_GROUPS[memoryGroup].events){loadEvents();return}
  renderMemoryList();
});
$('memory-reload').onclick=()=>{memoryItems=[];memoryCursor='';memoryLoaded=false;loadMemoryCatalog(true)};
document.querySelectorAll('[data-page]').forEach(button=>button.onclick=async()=>{
  if(detachedViews.has(button.dataset.page)){window.veilDesktop.focusView(button.dataset.page);return}
  if(document.body.dataset.currentPage==='peter'&&button.dataset.page!=='peter'&&window.peterLeaveGuard){
    const choice=await window.peterLeaveGuard();
    if(choice==='cancel')return;
  }
  setPage(button.dataset.page);
  if(button.dataset.page==='memory'&&memoryAvailable&&!memoryLoaded&&!memoryLoading)loadMemoryCatalog(true);
  if(button.dataset.page==='mcp')loadMCP();
  if(button.dataset.page==='life')loadLife();
  if(button.dataset.page==='peter'&&window.peterLoadPage)window.peterLoadPage();
  if(button.dataset.page==='instructions')loadInstructions();
});
document.querySelectorAll('[data-rail-page]').forEach(button=>button.onclick=()=>{
  const page=button.dataset.railPage;
  if(detachedViews.has(page)){window.veilDesktop.focusView(page);return}
  setPage(page);
  if(page==='life')loadLife();
});
const sidebarToggle=$('sidebar-toggle');
function setSidebarCollapsed(collapsed){
  document.body.classList.toggle('sidebar-collapsed',collapsed);
  sidebarToggle.setAttribute('aria-label',collapsed?'展开侧栏':'收起侧栏');sidebarToggle.title=collapsed?'展开侧栏':'收起侧栏';
  sidebarToggle.firstElementChild.dataset.lucide=collapsed?'panel-left-open':'panel-left-close';window.veilIconify(sidebarToggle);
}
try{setSidebarCollapsed(localStorage.getItem('veil-sidebar-collapsed')==='1'||localStorage.getItem('veil-sidebar-collapsed')===null)}catch{setSidebarCollapsed(true)}
sidebarToggle.onclick=()=>{const collapsed=!document.body.classList.contains('sidebar-collapsed');setSidebarCollapsed(collapsed);try{localStorage.setItem('veil-sidebar-collapsed',collapsed?'1':'0')}catch{}};
const railToggle=$('rail-toggle');
function setRailVisible(visible){
  document.body.classList.toggle('rail-hidden',!visible);
  document.body.classList.toggle('rail-visible',visible);
  railToggle.setAttribute('aria-pressed',visible?'true':'false');
  railToggle.title=visible?'隐藏今日与 Peter 的活动':'显示今日与 Peter 的活动';
  railToggle.setAttribute('aria-label',railToggle.title);
  railToggle.firstElementChild.dataset.lucide=visible?'panel-right-close':'panel-right-open';window.veilIconify(railToggle);
}
try{
  const savedRail=localStorage.getItem('veil-rail-visible');
  setRailVisible(savedRail==='1');
}catch{setRailVisible(false)}
railToggle.onclick=()=>{
  const visible=railToggle.getAttribute('aria-pressed')!=='true';
  setRailVisible(visible);
  try{localStorage.setItem('veil-rail-visible',visible?'1':'0')}catch{}
};
function syncDesktopRail(){
  const syncText=(id,value)=>{if($(id).textContent!==value)$(id).textContent=value};
  syncText('sidebar-next-wake',$('header-next-wake').textContent);
  syncText('rail-next-wake',$('header-next-wake').textContent);
  syncText('rail-activity-status',$('activity-work-status').textContent);
  syncText('rail-activity-count',$('activity-count').textContent);
  syncText('rail-life-count',$('life-count').textContent);
  const dotClass='rail-dot '+$('activity-work-dot').className.replace('status-dot','').trim();if($('rail-activity-dot').className!==dotClass)$('rail-activity-dot').className=dotClass;
  const approval=[...document.querySelectorAll('.approval:not(.done)')].at(-1);
  const pending=Array.isArray(pendingApprovalItems)?pendingApprovalItems.filter(x=>x.status==='pending'):[];
  $('rail-approval').hidden=!approval&&!pending.length;
  const railB=$('rail-approval').querySelector('b'),railSmall=$('rail-approval').querySelector('small');
  const setRailText=(el,value)=>{if(el&&el.textContent!==value)el.textContent=value};
  if(pending.length){setRailText(railB,pending.length+' 件需要你决定');setRailText(railSmall,'打开活动页处理');}
  else {setRailText(railB,'Peter 想运行一个操作');setRailText(railSmall,'回聊天里确认');}
}
new MutationObserver(syncDesktopRail).observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['class']});
$('rail-approval-open').onclick=()=>{if(pendingApprovalItems.filter(x=>x.status==='pending').length){setPage('activity');return}setPage('chat');[...document.querySelectorAll('.approval:not(.done)')].at(-1)?.scrollIntoView({behavior:'smooth',block:'center'})};
loadPendingApprovals(true);
syncDesktopRail();
let railDateTimer;
function refreshRailDate(){const now=new Date();$('rail-date-label').textContent=`${now.getMonth()+1} 月 ${now.getDate()} 日`;$('rail-weekday').textContent='星期'+'日一二三四五六'[now.getDay()];clearTimeout(railDateTimer);railDateTimer=setTimeout(refreshRailDate,new Date(now.getFullYear(),now.getMonth(),now.getDate()+1).getTime()-now.getTime()+50)}
refreshRailDate();
const composerMore=$('composer-more'),composerNode=$('composer');
const composerTray=$('composer-tray'),stickerPanel=$('sticker-panel');
let composerPanel=null,composerFollowingBottom=true;
function setComposerPanel(panel){composerPanel=panel;if(panel!=='stickers')stickerManage=false;composerNode.classList.toggle('tools-open',panel==='plus');composerNode.classList.toggle('stickers-open',panel==='stickers');composerMore.setAttribute('aria-expanded',String(panel!==null));composerTray.hidden=panel!=='plus';stickerPanel.hidden=panel!=='stickers';$('sticker-button').setAttribute('aria-expanded',String(panel==='stickers'))}
box.addEventListener('focus',()=>{if(composerPanel)setComposerPanel(null)});
imageFile.addEventListener('cancel',()=>setComposerPanel(null));
composerMore.onclick=event=>{event.stopPropagation();setComposerPanel(composerPanel?null:'plus')};
log.addEventListener('scroll',()=>{composerFollowingBottom=log.scrollHeight-log.scrollTop-log.clientHeight<80});
new ResizeObserver(()=>{if(composerFollowingBottom)requestAnimationFrame(()=>{log.scrollTop=log.scrollHeight})}).observe($('composer-footer'));
document.addEventListener('click',event=>{if(!composerNode.contains(event.target))setComposerPanel(null)});
const virtualDialog=$('virtual-card-dialog'),virtualForm=$('virtual-card-form');
let virtualCardKind='transfer',virtualClientId='';
function openVirtualCard(kind){
  virtualCardKind=kind;
  virtualClientId=(crypto.randomUUID?crypto.randomUUID():Math.random().toString(36).slice(2)+Date.now().toString(36)).replace(/[^A-Za-z0-9_-]/g,'').slice(0,64);
  if(virtualClientId.length<8)virtualClientId=('card'+Date.now().toString(36)+'00000000').slice(0,16);
  const transfer=kind==='transfer';
  $('virtual-card-title').textContent=transfer?'发送虚拟转账':'送一份虚拟礼物';
  $('virtual-card-submit').textContent=transfer?'发送转账':'送出礼物';
  $('virtual-card-amount-label').hidden=!transfer;$('virtual-card-note-label').hidden=!transfer;
  $('virtual-card-icon-label').hidden=transfer;$('virtual-card-gift-label').hidden=transfer;
  $('virtual-card-amount').required=transfer;$('virtual-card-content').required=!transfer;
  $('virtual-card-amount').value='';$('virtual-card-note').value='';$('virtual-card-icon').value='🎁';$('virtual-card-content').value='';
  setComposerPanel(null);virtualDialog.showModal();(transfer?$('virtual-card-amount'):$('virtual-card-icon')).focus();
}
$('transfer-button').onclick=()=>openVirtualCard('transfer');
$('gift-button').onclick=()=>openVirtualCard('gift');
$('virtual-card-close').onclick=()=>virtualDialog.close();
virtualForm.onsubmit=async event=>{
  event.preventDefault();
  const transfer=virtualCardKind==='transfer';
  const card=transfer
    ?{amount:$('virtual-card-amount').value.trim(),note:$('virtual-card-note').value,client_id:virtualClientId}
    :{icon:$('virtual-card-icon').value.trim()||'🎁',content:$('virtual-card-content').value,client_id:virtualClientId};
  const submit=$('virtual-card-submit');submit.disabled=true;
  try{
    const r=await api('/api/message/action',{action:transfer?'create_transfer':'create_gift',card});
    const data=await r.json();
    if(!r.ok||!data||data.ok===false){add('error',(data&&data.message)||'卡片发送失败，重试');return}
    virtualDialog.close();
  }catch(error){add('error',error.message||'卡片发送失败，重试')}
  finally{submit.disabled=false}
};
$('memory-panel').onclick=()=>window.veilDesktop.openMemory();
document.querySelectorAll('[data-cmd="/cost"]').forEach(button=>button.onclick=()=>{setPage('chat');send('/cost')});
$('memory-panel').title='打开 Ombre 高级记忆';
col.addEventListener('click',event=>{
  if(multiSelect){
    const message=event.target.closest('.msg[data-message-id]');
    if(message&&!event.target.closest('button,a,input')){
      event.preventDefault();
      if(selectedMessageIds.has(message.dataset.messageId))selectedMessageIds.delete(message.dataset.messageId);
      else selectedMessageIds.add(message.dataset.messageId);
      updateSelectionBar();refreshMessageSelection();return;
    }
  }
  const button=event.target.closest('.approval button[data-answer]');
  if(!button)return;
  const card=button.closest('.approval');
  approve(card,card.querySelector('.status'),button.dataset.answer,Number(card.dataset.id));
});
$('selection-all').onclick=()=>{selectedMessageIds=new Set([...document.querySelectorAll('.msg[data-message-id]')].map(node=>node.dataset.messageId));updateSelectionBar();refreshMessageSelection()};
$('selection-delete').onclick=()=>showMessageDeleteConfirm(null,[...selectedMessageIds]);
$('selection-cancel').onclick=()=>{multiSelect=false;selectedMessageIds.clear();updateSelectionBar();refreshMessageSelection()};
document.querySelector('#collapse').onclick=()=>window.veilDesktop.collapse();
document.querySelector('#expand').onclick=()=>window.veilDesktop.expand();
$('merge-activity').onclick=()=>window.veilDesktop.mergeView('activity');
$('merge-view').onclick=()=>detachedView&&window.veilDesktop.mergeView(detachedView);
document.querySelectorAll('[data-activity-filter]').forEach(button=>button.onclick=()=>{
  activityFilter=button.dataset.activityFilter;
  document.querySelectorAll('[data-activity-filter]').forEach(item=>item.classList.toggle('active',item===button));
  renderActivityTimeline();
});
function applyDetachedViews(views){
  detachedViews=new Set(Array.isArray(views)?views:[]);document.body.dataset.detachedViews=[...detachedViews].join(' ');
  document.body.classList.toggle('activity-detached',detachedViews.has('activity')&&!detachedView);
  $('merge-activity').hidden=detachedView!=='activity';
}
window.veilDesktop.onDetachedViews(applyDetachedViews);
window.veilDesktop.detachedViews().then(applyDetachedViews).catch(()=>{});
let tabDrag=null;
document.querySelectorAll('[data-page]').forEach(tab=>tab.addEventListener('pointerdown',event=>{
  if(detachedView||detachedViews.has(tab.dataset.page)||event.button!==0)return;
  tabDrag={x:event.screenX,y:event.screenY,view:tab.dataset.page,tab};tab.setPointerCapture(event.pointerId);
}));
document.querySelectorAll('[data-page]').forEach(tab=>tab.addEventListener('pointermove',event=>{
  if(!tabDrag||tabDrag.tab!==tab)return;
  const moved=Math.hypot(event.screenX-tabDrag.x,event.screenY-tabDrag.y)>28;
  const outside=event.clientX<0||event.clientY<0||event.clientX>innerWidth||event.clientY>innerHeight;
  if(moved&&outside){const view=tabDrag.view;tabDrag=null;window.veilDesktop.detachView(view,{x:event.screenX,y:event.screenY})}
}));
document.querySelectorAll('[data-page]').forEach(tab=>{tab.addEventListener('pointerup',()=>{tabDrag=null});tab.addEventListener('pointercancel',()=>{tabDrag=null})});
window.veilDesktop.onShowActivity(()=>setPage('activity'));
window.veilDesktop.onShowView(view=>{setPage(view);if(view==='life')loadLife()});
window.veilDesktop.onShowSettings(()=>setPage('more'));
if(detachedView&&['chat','activity','context','memory','life','mcp','more'].includes(detachedView)){
  document.body.classList.add('detached-window');$('detached-header').hidden=false;
  $('detached-title').textContent='Peter · '+({chat:'聊天',activity:'活动记录',context:'实时上下文',memory:'记忆',life:'生活',mcp:'MCP',more:'设置'}[detachedView]);setPage(detachedView);
}else{setPage('chat');box.focus()}
setBusy(true);poll();

let mcpView={enabled:false,services:[]},mcpEditing=null,mcpLoading=false,mcpReady=false,mcpSaving=false,mcpSaveQueue=Promise.resolve(),mcpGeneration=0,mcpSignature='',mcpActionError='';
$('mcp-add').disabled=true;$('mcp-enabled').disabled=true;
function mcpConfig(source=mcpView){return {enabled:source.enabled,services:source.services.map(({id,builtin,adapter,managed,direct_grant,command,args,cwd,enabled,permission})=>builtin?{id,builtin:true,adapter,enabled}:{id,managed,direct_grant,command,args,cwd,enabled,permission})}}
async function loadMCP(force=false){
  if(mcpLoading||mcpSaving)return;
  const generation=mcpGeneration;
  mcpLoading=true;if(!mcpReady||force)$('mcp-status').textContent='读取服务状态中…';
  try{
    const response=await api(force?'/api/mcp?retry=1':'/api/mcp');const data=await response.json();
    if(generation!==mcpGeneration)return;
    if(!response.ok)throw new Error(data.error||'读取失败');
    const signature=JSON.stringify(data);
    mcpReady=true;if(force)mcpActionError='';
    if(signature!==mcpSignature){mcpView=data;mcpSignature=signature;renderMCP()}
    else if(force)$('mcp-status').textContent=mcpActionError||mcpView.error||`${mcpView.services.length} 个服务`;
  }
  catch(error){if(generation===mcpGeneration&&(!mcpReady||force))$('mcp-status').textContent=error.message}
  finally{mcpLoading=false}
}
function saveMCP(change){
  const task=mcpSaveQueue.then(()=>saveMCPNow(change));mcpSaveQueue=task.catch(()=>{});return task;
}
async function saveMCPNow(change){
  if(!mcpReady){$('mcp-status').textContent='等配置读完';return false}
  mcpSaving=true;mcpGeneration++;
  $('mcp-status').textContent='正在保存…';
  try{
    for(let attempt=0;attempt<3;attempt++){
      const currentResponse=await api('/api/mcp');const current=await currentResponse.json();
      if(!currentResponse.ok)throw new Error(current.error||'读取失败');
      const next=change(mcpConfig(current));
      const response=await api('/api/mcp',{...next,revision:current.revision},30000);const data=await response.json();
      if(response.status===409)continue;
      if(!response.ok)throw new Error(data.error||'保存失败');
      mcpView=data;mcpSignature=JSON.stringify(data);mcpActionError='';renderMCP();return true;
    }
    throw new Error('别处正在改配置');
  }
  catch(error){mcpActionError=error.message;$('mcp-status').textContent=error.message;return false}
  finally{mcpSaving=false;mcpGeneration++}
}
function renderMCP(){
  $('mcp-add').disabled=!mcpReady;$('mcp-enabled').disabled=!mcpReady;
  $('mcp-enabled').checked=mcpView.enabled;
  $('mcp-status').textContent=mcpActionError||mcpView.error||`${mcpView.services.length} 个服务`;
  const list=$('mcp-list');list.replaceChildren();
  if(!mcpView.services.length){const p=document.createElement('p');p.className='muted';p.textContent='还没有 MCP 服务';list.append(p)}
  for(const service of mcpView.services){
    const card=document.createElement('article');card.className='surface mcp-card';
    const heading=document.createElement('div');heading.className='section-heading';
    const title=document.createElement('h3');title.textContent=service.id;
    const status=document.createElement('span');status.className='state-pill';status.textContent=service.builtin?(service.status==='connected'?'已启用':'已停用'):service.status==='connected'?'已连接':service.status==='connecting'?'连接中':service.status==='error'?'连接失败':'已停用';
    heading.append(title,status);card.append(heading);
    const details=document.createElement('p');details.textContent=service.managed==='nowhere'?`${service.tools.length} 个工具 · Peter 的独立旅程存档 · ${service.direct_grant?'已授权直接调用':'直接调用授权已撤销，重新启用需确认'} · 旋复 / 乌有乡 · CC BY-NC 4.0（含 VEIL 兼容修正）`:service.builtin?`${service.tools.length} 个工具 · VEIL 内置集成`:`${service.tools.length} 个工具 · ${service.permission==='ask'?'每次询问':'直接调用'}`;card.append(details);
    if(service.managed==='nowhere'){
      const project=document.createElement('p');project.className='mcp-tools';project.textContent='项目与许可：https://github.com/yuyixuanfu/nowhere · https://creativecommons.org/licenses/by-nc/4.0/';card.append(project);
    }
    if(service.error){const error=document.createElement('p');error.className='mcp-error';error.textContent=service.error;card.append(error)}
    if(service.tools.length){const tools=document.createElement('p');tools.className='mcp-tools';tools.textContent=service.tools.join('、');card.append(tools)}
    const actions=document.createElement('div');actions.className='tool-row';
    if(service.managed==='nowhere'&&service.observer_url){const observe=document.createElement('button');observe.type='button';observe.textContent='查看旅程地图';observe.onclick=()=>window.veilDesktop.openNowhere(service.observer_url);actions.append(observe)}
    const toggle=document.createElement('button');toggle.type='button';toggle.textContent=service.enabled?(service.managed==='nowhere'?'停用并撤销授权':'停用'):'启用';toggle.onclick=()=>{const grantTravel=service.managed==='nowhere'&&!service.enabled;if(grantTravel&&!confirm('让 Peter 直接调用乌有乡工具，改他自己的地点和旅程。继续？'))return;saveMCP(current=>({...current,services:current.services.map(item=>item.id===service.id?{...item,enabled:!service.enabled,direct_grant:service.managed==='nowhere'?(service.enabled?false:grantTravel):item.direct_grant}:item)}))};
    actions.append(toggle);
    if(!service.builtin&&!service.managed){const edit=document.createElement('button');edit.type='button';edit.textContent='编辑';edit.onclick=()=>openMCPForm(service);
      const remove=document.createElement('button');remove.type='button';remove.textContent='移除';remove.onclick=()=>{if(confirm(`移除 MCP 服务 ${service.id}？`))saveMCP(current=>({...current,services:current.services.filter(item=>item.id!==service.id)}))};actions.append(edit,remove)}
    card.append(actions);list.append(card);
  }
  if(mcpView.services.some(service=>service.status==='connecting'))setTimeout(()=>{if(document.body.dataset.currentPage==='mcp')loadMCP()},700);
}
function openMCPForm(service=null){
  mcpEditing=service?.id||null;$('mcp-form').hidden=false;$('mcp-form-heading').textContent=service?'编辑本地服务':'添加本地服务';
  $('mcp-id').value=service?.id||'';$('mcp-id').readOnly=Boolean(service);
  $('mcp-command').value=service?.command||'';$('mcp-args').value=(service?.args||[]).join('\n');$('mcp-cwd').value=service?.cwd||'';
  $('mcp-permission').value=service?.permission||'ask';syncVeilSelect($('mcp-permission'));$('mcp-id').focus();
}
$('mcp-add').onclick=()=>{if(mcpReady)openMCPForm()};$('mcp-cancel').onclick=()=>{$('mcp-form').hidden=true};$('mcp-refresh').onclick=()=>loadMCP(true);
$('mcp-enabled').onchange=async()=>{const checked=$('mcp-enabled').checked;if(!await saveMCP(current=>({...current,enabled:checked})))$('mcp-enabled').checked=mcpView.enabled};
$('mcp-form').onsubmit=async event=>{
  event.preventDefault();if(!mcpReady)return;
  const service={id:$('mcp-id').value.trim(),command:$('mcp-command').value.trim(),args:$('mcp-args').value.split(/\r?\n/).filter(Boolean),cwd:$('mcp-cwd').value.trim()||null,enabled:true,permission:$('mcp-permission').value};
  const editingId=mcpEditing;
  if(await saveMCP(current=>{const services=editingId?current.services.map(item=>item.id===editingId?{...service,enabled:item.enabled}:item):[...current.services,service];return {...current,services}})){$('mcp-form').hidden=true;mcpEditing=null}
};
if(detachedView==='mcp')loadMCP();
if(detachedView==='life')loadLife();
setInterval(()=>{if(document.body.dataset.currentPage==='life'&&!document.hidden)loadLife()},15000);
setInterval(()=>{if(document.body.dataset.currentPage==='mcp'&&!mcpLoading&&$('mcp-form').hidden)loadMCP()},3000);

// Ticket 134: 模型 settings card and the header switch. The server keeps keys; this page only sees "…1234".
let modelsView=null,modelsSignature='',editingEndpoint=null,editingModel=null,modelBilling='call',modelsSaving=false;
function modelName(m){return (m.label||m.model)+(m.builtin?'':' · '+m.endpoint_name)}
function modelPrice(m){
  if(m.builtin)return '内置，价格在 character.toml';
  return m.billing==='call'?'每次 ¥'+m.cny_per_call:'输入 ¥'+m.input_cny_per_million+' / 输出 ¥'+m.output_cny_per_million+'（每百万 tokens）';
}
function fillModelSelect(select,view,value,followOption){
  const follow=followOption?[(()=>{const o=document.createElement('option');o.value='follow-chat';o.textContent='跟随聊天模型';return o})()]:[];
  const optional=select.id==='use-delegate'?[(()=>{const o=document.createElement('option');o.value='';o.textContent='关闭委派';return o})()]:[];
  const placeholder=!value&&select.id!=='use-delegate'?[(()=>{const o=document.createElement('option');o.value='';o.textContent='请选择模型';o.disabled=true;return o})()]:[];
  select.replaceChildren(...optional,...placeholder,...follow,...view.models.map(m=>{const o=document.createElement('option');o.value=m.id;o.textContent=modelName(m)+(m.key_set?'':'（没有 key）');return o}));
  select.value=value;
  syncVeilSelect(select);
}
function modelItem(title,detail,onEdit,onDelete){
  const row=document.createElement('div');row.className='model-item';
  const text=document.createElement('div');const strong=document.createElement('strong');strong.textContent=title;const small=document.createElement('small');small.textContent=detail;text.append(strong,small);
  const actions=document.createElement('div');actions.className='tool-row';
  if(onEdit){const b=document.createElement('button');b.type='button';b.textContent='编辑';b.onclick=onEdit;actions.append(b)}
  if(onDelete){const b=document.createElement('button');b.type='button';b.className='danger';b.textContent='删除';b.onclick=()=>{if(b.dataset.confirm){onDelete()}else{b.dataset.confirm='1';b.textContent='再点一次删除';setTimeout(()=>{delete b.dataset.confirm;b.textContent='删除'},4000)}};actions.append(b)}
  row.append(text,actions);return row;
}
function renderModels(view){
  const card=$('models-card'),sw=$('model-switch');
  if(!view){card.hidden=true;sw.disabled=true;return}
  card.hidden=false;sw.disabled=modelsSaving||stopping||stopped;
  const signature=JSON.stringify(view);
  if(signature===modelsSignature)return;
  modelsSignature=signature;modelsView=view;
  fillModelSelect(sw,view,view.use.peter);
  fillModelSelect($('use-peter'),view,view.use.peter);fillModelSelect($('use-autonomy'),view,view.use.autonomy,true);fillModelSelect($('use-delegate'),view,view.use.delegate||'');fillModelSelect($('use-background'),view,view.use.background);fillModelSelect($('use-glance'),view,view.use.glance,true);
  $('models-migration').textContent=view.migration_notice||'';$('models-migration').hidden=!view.migration_notice;
  $('background-hint').textContent=view.background_restart?'重启后生效':'Ombre 给记忆打标签、整理短期便签用这个。换了以后，完全退出 Peter 再打开才生效。';
  if(document.activeElement!==$('limit-daily'))$('limit-daily').value=view.limits.daily_cny;
  if(document.activeElement!==$('limit-monthly'))$('limit-monthly').value=view.limits.monthly_cny;
  const endpoints=$('endpoint-list');endpoints.replaceChildren();
  if(!view.endpoints.length){const p=document.createElement('p');p.className='muted';p.textContent='还没有接口';endpoints.append(p)}
  for(const e of view.endpoints)endpoints.append(modelItem(e.name,e.base_url+' · '+(e.key_set?'key 已保存 '+e.key_hint:'没有 key'),()=>openEndpointForm(e),
    ()=>saveModels({endpoints:modelsPayload().endpoints.filter(x=>x.id!==e.id),models:modelsPayload().models.filter(m=>m.endpoint!==e.id)},'接口已删除。')));
  const models=$('model-list');models.replaceChildren();
  for(const m of view.models)models.append(modelItem(modelName(m),m.model+' · '+modelPrice(m)+(m.builtin?'':' · 思考'+(m.thinking?'开':'关')),
    m.builtin?null:()=>openModelForm(m),m.builtin?null:()=>saveModels({models:modelsPayload().models.filter(x=>x.id!==m.id)},'模型已删除。')));
  $('add-model').disabled=!view.endpoints.length;
}
function modelsPayload(){
  const v=modelsView;
  const use={...v.use};if(!v.use_explicit?.autonomy)delete use.autonomy;
  return {endpoints:v.endpoints.map(e=>({id:e.id,name:e.name,base_url:e.base_url})),
    models:v.models.filter(m=>!m.builtin).map(m=>{const out={};for(const k of ['id','endpoint','model','label','thinking','billing','cny_per_call','input_cny_per_million','cached_input_cny_per_million','output_cny_per_million','max_input_tokens'])if(m[k]!==undefined)out[k]=m[k];return out}),
    use,limits:{...v.limits}};
}
function showModelsError(text){const el=$('models-error');el.textContent=text||'';el.hidden=!text}
async function saveModels(change,done){
  if(!modelsView)return false;
  if(modelsSaving){showModelsError('保存中，稍后再点');return false}
  modelsSaving=true;showModelsError('');
  try{
    const r=await api('/api/models',{...modelsPayload(),...change});const data=await r.json();
    if(!r.ok){const msg=(data&&data.message)||'保存失败，检查 Agent 连接';showModelsError(msg);add('error',msg);return false}
    modelsSignature='';renderModels(data.models);if(done)add('notice',done);return true;
  }catch(e){const msg='保存失败，检查 Agent 连接';showModelsError(msg);add('error',msg);return false}
  finally{modelsSaving=false;$('model-switch').disabled=stopping||stopped}
}
async function useModel(role,id){
  if(!modelsView||modelsSaving||id===modelsView.use[role])return;
  modelsSaving=true;
  try{
    const r=await api('/api/models/use',{role,model:id});const data=await r.json();
    if(!r.ok){add('error',(data&&data.message)||'切换失败，重试');modelsSignature='';renderModels(modelsView);return}
    modelsSignature='';renderModels(data.models);
    const m=data.models.models.find(x=>x.id===id);
    if(role==='glance'){add('notice',id==='follow-chat'?'已跟随聊天模型':'瞄一眼改用 '+modelName(m)+'，下一次瞄一眼生效。');return}
    if(role==='autonomy'){add('notice','自主活动改用 '+modelName(m)+'，下次自主活动起生效');return}
    if(role==='delegate'){add('notice',id?'委派任务改用 '+modelName(m)+'。':'委派任务已关');return}
    add('notice',role==='peter'?'下一句起，Peter 用 '+modelName(m)+'。':'记忆后台改成 '+modelName(m)+'，重启后生效');
  }catch(e){add('error','切换失败，检查 Agent 连接')}
  finally{modelsSaving=false;$('model-switch').disabled=stopping||stopped}
}
$('model-switch').onchange=e=>useModel('peter',e.target.value);
$('use-peter').onchange=e=>useModel('peter',e.target.value);
$('use-autonomy').onchange=e=>useModel('autonomy',e.target.value);
$('use-delegate').onchange=e=>useModel('delegate',e.target.value);
$('use-background').onchange=e=>useModel('background',e.target.value);
$('use-glance').onchange=e=>useModel('glance',e.target.value);
$('save-limits').onclick=()=>saveModels({limits:{daily_cny:$('limit-daily').value,monthly_cny:$('limit-monthly').value}},'上限已保存');
function openEndpointForm(e){
  editingEndpoint=e||null;$('endpoint-form').hidden=false;$('add-endpoint').hidden=true;
  $('endpoint-name').value=e?e.name:'';$('endpoint-url').value=e?e.base_url:'';$('endpoint-key').value='';
  $('endpoint-key-hint').hidden=!e;$('endpoint-name').focus();
}
function closeEndpointForm(){showModelsError('');$('endpoint-form').hidden=true;$('add-endpoint').hidden=false;$('endpoint-key').value='';editingEndpoint=null}
$('add-endpoint').onclick=()=>openEndpointForm(null);
$('endpoint-cancel').onclick=closeEndpointForm;
$('endpoint-form').onsubmit=async ev=>{
  ev.preventDefault();
  const item={name:$('endpoint-name').value,base_url:$('endpoint-url').value};
  if($('endpoint-key').value.trim())item.key=$('endpoint-key').value.trim();
  const endpoints=modelsPayload().endpoints;
  if(editingEndpoint){const i=endpoints.findIndex(x=>x.id===editingEndpoint.id);endpoints[i]={...endpoints[i],...item}}else endpoints.push(item);
  if(await saveModels({endpoints},editingEndpoint?'接口已更新。':'接口已添加'))closeEndpointForm();
};
function setBilling(kind){
  modelBilling=kind;
  document.querySelectorAll('[data-billing]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.billing===kind)));
  document.querySelectorAll('[data-billing-field]').forEach(f=>f.hidden=f.dataset.billingField!==kind);
}
document.querySelectorAll('[data-billing]').forEach(b=>b.onclick=()=>setBilling(b.dataset.billing));
function openModelForm(m){
  editingModel=m||null;$('model-form').hidden=false;$('add-model').hidden=true;
  const select=$('model-endpoint');select.replaceChildren(...modelsView.endpoints.map(e=>{const o=document.createElement('option');o.value=e.id;o.textContent=e.name;return o}));
  select.value=m?m.endpoint:modelsView.endpoints[0].id;syncVeilSelect(select);
  $('model-name').value=m?m.model:'';$('model-label').value=m?m.label:'';
  $('model-per-call').value=m&&m.cny_per_call||'';$('model-input-price').value=m&&m.input_cny_per_million||'';
  $('model-cached-price').value=m&&m.billing==='tokens'&&m.cached_input_cny_per_million!==m.input_cny_per_million?m.cached_input_cny_per_million:'';
  $('model-output-price').value=m&&m.output_cny_per_million||'';$('model-max-input').value=m?m.max_input_tokens:32768;
  $('model-thinking').checked=m?m.thinking:true;setBilling(m?m.billing:'call');$('model-name').focus();
}
function closeModelForm(){showModelsError('');$('model-form').hidden=true;$('add-model').hidden=false;editingModel=null}
$('add-model').onclick=()=>openModelForm(null);
$('model-cancel').onclick=closeModelForm;
$('model-form').onsubmit=async ev=>{
  ev.preventDefault();
  const item={endpoint:$('model-endpoint').value,model:$('model-name').value,label:$('model-label').value,thinking:$('model-thinking').checked,
    billing:modelBilling,max_input_tokens:Number($('model-max-input').value)||32768};
  if(modelBilling==='call')item.cny_per_call=$('model-per-call').value;
  else{item.input_cny_per_million=$('model-input-price').value;item.output_cny_per_million=$('model-output-price').value;if($('model-cached-price').value!=='')item.cached_input_cny_per_million=$('model-cached-price').value}
  const models=modelsPayload().models;
  if(editingModel){const i=models.findIndex(x=>x.id===editingModel.id);models[i]={id:editingModel.id,...item}}else models.push(item);
  if(await saveModels({models},editingModel?'模型已更新。':'模型已添加'))closeModelForm();
};

// ---- Ticket 222: stickers (表情包). In chat a sticker is the text "[表情包：图集/名字]"; the window draws the picture. ----
var STICKER_MARK=/^\[表情包：([^\[\]\/\n]{1,30})\/([^\[\]\n]{1,40})\]\n?([\s\S]*)$/;
var stickerLib=null,stickerLoading=null,stickerPackId=null,stickerManage=false;
function loadStickers(force=false){
  if(stickerLib&&!force)return Promise.resolve(stickerLib);
  if(!stickerLoading)stickerLoading=api('/api/stickers').then(async r=>{const d=await r.json();if(!r.ok)throw new Error(d.error||'表情包读取失败');stickerLib=d;return d}).finally(()=>{stickerLoading=null});
  return stickerLoading;
}
function findSticker(lib,pack,name){
  const p=(lib.packs||[]).find(x=>x.name.toLowerCase()===pack.trim().toLowerCase());
  return p&&p.stickers.find(s=>s.name.toLowerCase()===name.trim().toLowerCase())||null;
}
function stickerBubble(d,text){
  if(!canLoadChatMedia)return false;
  const m=STICKER_MARK.exec(text||'');if(!m)return false;
  d.classList.add('sticker-msg');
  const holder=document.createElement('div');holder.className='msg-sticker-wrap';
  const label=document.createElement('span');label.className='msg-sticker-label';label.textContent=`表情包：${m[1]}/${m[2]}`;
  holder.appendChild(label);d.appendChild(holder);
  const caption=m[3]&&m[3].trim();if(caption){const c=document.createElement('div');c.innerHTML=render(caption);d.appendChild(c)}
  const show=lib=>{
    const s=findSticker(lib,m[1],m[2]);if(!s)return;
    window.veilDesktop.stickerUrl(s.file).then(url=>{const img=document.createElement('img');img.className='msg-sticker';img.alt=`${m[1]}/${m[2]}`;img.loading='lazy';img.onload=()=>{label.remove()};img.src=url;holder.appendChild(img)}).catch(()=>{});
  };
  loadStickers().then(lib=>{if(findSticker(lib,m[1],m[2]))show(lib);else loadStickers(true).then(show).catch(()=>{})}).catch(()=>{});
  return true;
}
const stickerButton=$('sticker-button'),stickerPacks=$('sticker-packs'),stickerGrid=$('sticker-grid'),stickerTools=$('sticker-tools'),stickerStatus=$('sticker-status');
function stickerSay(text){stickerStatus.textContent=text||'';stickerStatus.hidden=!text}
async function stickerPost(body){
  const r=await api('/api/stickers',body,120000);const d=await r.json();
  if(!r.ok)throw new Error(d.error||'没成功');
  stickerLib={packs:d.packs};return d;
}
function stickerCurrentPack(){const packs=stickerLib?.packs||[];return packs.find(p=>p.id===stickerPackId)||packs[0]||null}
function renderStickers(){
  const pack=stickerCurrentPack();stickerPackId=pack?.id||null;
  stickerPacks.replaceChildren();
  for(const p of stickerLib?.packs||[]){
    const b=document.createElement('button');b.type='button';b.className='sticker-tab';b.role='tab';b.setAttribute('aria-selected',String(p.id===stickerPackId));
    b.textContent=p.name;b.title=`${p.name}，${p.stickers.length} 个表情`;b.onclick=event=>{event.stopPropagation();stickerPackId=p.id;renderStickers()};stickerPacks.appendChild(b);
  }
  stickerGrid.replaceChildren();
  if(!pack){const e=document.createElement('div');e.className='sticker-empty';const p=document.createElement('p');p.textContent='还没有表情包';const b=document.createElement('button');b.type='button';b.textContent='导入表情包';b.onclick=event=>{event.stopPropagation();stickerManage=true;renderStickers()};e.append(p,b);stickerGrid.appendChild(e)}
  else if(!pack.stickers.length){const e=document.createElement('p');e.className='sticker-empty';e.textContent='还没有表情';stickerGrid.appendChild(e)}
  else for(const s of pack.stickers){
    const tile=document.createElement(stickerManage?'div':'button');tile.className=stickerManage?'sticker-tile':'sticker-cell';tile.title=s.name;
    if(!stickerManage){tile.type='button';tile.setAttribute('aria-label',`发送表情包 ${s.name}`)}
    const img=document.createElement('img');img.alt=s.name;img.loading='lazy';
    window.veilDesktop.stickerUrl(s.file).then(url=>{img.src=url}).catch(()=>{});
    if(stickerManage){
      const name=document.createElement('input');name.value=s.name;name.maxLength=10;name.title='改名，最多 10 字';
      name.onkeydown=e=>{if(e.key==='Enter')name.blur()};
      name.onchange=async()=>{try{await stickerPost({action:'rename',id:s.id,name:name.value});renderStickers()}catch(e){stickerSay(e.message);name.value=s.name}};
      const del=document.createElement('button');del.type='button';del.className='sticker-del';del.textContent='×';del.title='删除这张';
      del.onclick=async()=>{if(del.dataset.sure!=='1'){del.dataset.sure='1';del.textContent='确定？';setTimeout(()=>{del.dataset.sure='';del.textContent='×'},3000);return}try{await stickerPost({action:'delete',id:s.id});renderStickers()}catch(e){stickerSay(e.message)}};
      tile.append(img,name,del);
    }else{
      const label=document.createElement('span');label.textContent=s.name;
      tile.append(img,label);tile.onclick=()=>{if(selectedImage){stickerSay('先处理发送区里的图片');return}if(stopping||stopped||sending||voiceSending||speechIsActive()){stickerSay('发送或录音中，稍后再点');return}send(`[表情包：${pack.name}/${s.name}]`)};
    }
    stickerGrid.appendChild(tile);
  }
  stickerTools.hidden=!stickerManage;$('sticker-manage').setAttribute('aria-label',stickerManage?'完成管理':'管理表情包');$('sticker-manage').title=stickerManage?'完成管理':'管理表情包';$('sticker-manage').innerHTML=window.veilIcon(stickerManage?'check':'settings-2',18);
  $('sticker-pack-name').value=pack?.name||'';
}
async function openStickerPanel(){
  setComposerPanel('stickers');
  stickerSay('载入中…');
  try{await loadStickers(true)}catch(e){stickerSay(e.message);return}
  stickerSay('');
  renderStickers();
}
function closeStickerPanel(){setComposerPanel(null);stickerManage=false}
stickerButton.onclick=()=>{composerPanel==='stickers'?closeStickerPanel():openStickerPanel()};
$('sticker-manage').onclick=()=>{stickerManage=!stickerManage;stickerSay('');renderStickers()};
$('sticker-add-pack').onclick=async()=>{
  const input=$('sticker-new-pack');if(!input.value.trim())return;
  try{const d=await stickerPost({action:'add_pack',name:input.value});stickerPackId=d.pack.id;input.value='';stickerSay('');renderStickers()}catch(e){stickerSay(e.message)}
};
$('sticker-rename-pack').onclick=async()=>{const pack=stickerCurrentPack();if(!pack)return;try{await stickerPost({action:'rename_pack',id:pack.id,name:$('sticker-pack-name').value});stickerSay('');renderStickers()}catch(e){stickerSay(e.message)}};
$('sticker-delete-pack').onclick=async e=>{
  const pack=stickerCurrentPack();if(!pack)return;const b=e.currentTarget;
  if(b.dataset.sure!=='1'||b.dataset.packId!==pack.id){b.dataset.sure='1';b.dataset.packId=pack.id;b.textContent=`再点一次删除「${pack.name}」和里面的 ${pack.stickers.length} 张`;setTimeout(()=>{if(b.dataset.packId===pack.id){b.dataset.sure='';b.dataset.packId='';b.textContent='删除图集'}},4000);return}
  b.dataset.sure='';b.dataset.packId='';b.textContent='删除图集';
  try{await stickerPost({action:'delete_pack',id:pack.id});stickerPackId=null;renderStickers()}catch(err){stickerSay(err.message)}
};
$('sticker-add-links').onclick=async()=>{
  const pack=stickerCurrentPack(),box=$('sticker-links');
  if(!pack){stickerSay('先新建图集');return}
  if(!box.value.trim())return;
  const button=$('sticker-add-links');button.disabled=true;
  try{
    const parsed=await stickerPost({action:'parse',text:box.value});
    const rows=parsed.rows;let added=0,done=0;const failed=[];
    try{
      for(;done<rows.length;done+=10){
        stickerSay(`下载 ${Math.min(done+10,rows.length)}/${rows.length}…`);
        const d=await stickerPost({action:'add_links',pack:pack.id,rows:rows.slice(done,done+10)});
        added+=d.added;failed.push(...d.failed);
      }
    }catch(e){
      // Keep only what was not saved yet, so a retry never adds the same pictures twice.
      const left=[...failed,...rows.slice(done)].map(f=>`${f.name||''}：${f.url||''}`);
      box.value=left.join('\n');renderStickers();
      stickerSay(`中途出错：${e.message}。已加 ${added} 张，其余可重试`);
      return;
    }
    const skipped=parsed.skipped.length;
    box.value=failed.map(f=>`${f.name||''}：${f.url||''}`).join('\n');
    stickerSay(`添加了 ${added} 张`+(failed.length?`，${failed.length} 张没下载成（可重试）`:'')+(skipped?`，${skipped} 行看不懂，跳过`:''));
    renderStickers();
  }catch(e){stickerSay(e.message)}finally{button.disabled=false}
};
$('sticker-upload').onclick=()=>$('sticker-file').click();
$('sticker-file').onchange=async e=>{
  const pack=stickerCurrentPack();const files=[...e.target.files];e.target.value='';
  if(!pack||!files.length)return;let ok=0;const bad=[];
  for(const file of files){
    try{
      if(file.size>2*1024*1024)throw new Error('超过 2MB');
      const dataUrl=await fileAsDataUrl(file);
      const r=await api('/api/stickers/upload',{pack:pack.id,name:file.name.replace(/\.[^.]+$/,'').slice(0,10)||'表情',data:dataUrl.slice(dataUrl.indexOf(',')+1)},60000);
      const d=await r.json();if(!r.ok)throw new Error(d.error||'没成功');stickerLib={packs:d.packs};ok++;
    }catch(err){bad.push(`${file.name}（${err.message}）`)}
  }
  stickerSay((ok?`上传了 ${ok} 张`:'')+(bad.length?` 没成功：${bad.join('、')}`:''));renderStickers();
};

const veilSelectState=new WeakMap();let veilSelectMenu=null,veilSelectCurrent=null;
function syncVeilSelect(select){
  const state=veilSelectState.get(select);if(!state)return;
  const option=select.selectedOptions[0];state.trigger.textContent=option?.textContent||'请选择';state.trigger.disabled=select.disabled;
  state.menu.replaceChildren(...[...select.options].map((item,index)=>{const button=document.createElement('button');button.type='button';button.className='veil-select-option';button.role='option';button.textContent=item.textContent;button.disabled=item.disabled;button.setAttribute('aria-selected',String(index===select.selectedIndex));button.onclick=()=>{if(button.disabled)return;select.selectedIndex=index;select.dispatchEvent(new Event('input',{bubbles:true}));select.dispatchEvent(new Event('change',{bubbles:true}));closeVeilSelect(true)};return button}));
}
function closeVeilSelect(restoreFocus=false){if(!veilSelectCurrent)return;const state=veilSelectState.get(veilSelectCurrent);state.trigger.setAttribute('aria-expanded','false');state.menu.classList.remove('open');if(restoreFocus)state.trigger.focus();veilSelectCurrent=null}
function openVeilSelect(select,focusSelected=false){closeVeilSelect();const state=veilSelectState.get(select);if(!state||select.disabled)return;syncVeilSelect(select);veilSelectCurrent=select;const trigger=state.trigger,menu=state.menu,rect=trigger.getBoundingClientRect();menu.style.minWidth=Math.max(rect.width,Math.min(360,Math.max(...[...select.options].map(option=>[...option.textContent].length*8+32),240)) )+'px';menu.classList.add('open');menu.style.left=Math.max(8,Math.min(rect.left,innerWidth-menu.offsetWidth-8))+'px';const menuHeight=menu.offsetHeight,top=rect.bottom+6+menuHeight>innerHeight-8?Math.max(8,rect.top-menuHeight-6):rect.bottom+6;menu.style.top=top+'px';trigger.setAttribute('aria-expanded','true');if(focusSelected)(menu.querySelector('[aria-selected="true"]')||menu.querySelector('.veil-select-option'))?.focus()}
function enhanceVeilSelect(select){if(veilSelectState.has(select))return;const trigger=document.createElement('button');trigger.type='button';trigger.className='veil-select-trigger';trigger.setAttribute('aria-haspopup','listbox');trigger.setAttribute('aria-expanded','false');trigger.setAttribute('aria-label',select.getAttribute('aria-label')||select.closest('label')?.childNodes[0]?.textContent?.trim()||select.parentElement?.textContent?.replace(select.textContent,'').trim()||'选择');const menu=document.createElement('div');menu.className='veil-select-menu';menu.setAttribute('role','listbox');menu.id='veil-select-'+Math.random().toString(36).slice(2);trigger.setAttribute('aria-controls',menu.id);const wrapper=document.createElement('span');wrapper.className='veil-select';const parent=select.parentElement;select.classList.add('veil-select-native');select.setAttribute('aria-hidden','true');select.tabIndex=-1;if(parent.tagName==='LABEL'){parent.append(wrapper);parent.after(select)}else select.after(wrapper);wrapper.append(trigger);document.body.append(menu);const state={trigger,menu};veilSelectState.set(select,state);trigger.onclick=()=>veilSelectCurrent===select?closeVeilSelect():openVeilSelect(select);trigger.onkeydown=event=>{if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();openVeilSelect(select,true)}else if(event.key==='Escape')closeVeilSelect()};menu.onkeydown=event=>{const options=[...menu.querySelectorAll('.veil-select-option:not(:disabled)')],index=options.indexOf(document.activeElement);if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();options[Math.max(0,Math.min(options.length-1,index+(event.key==='ArrowDown'?1:-1))) ]?.focus()}else if(event.key==='Home'||event.key==='End'){event.preventDefault();options[event.key==='Home'?0:options.length-1]?.focus()}else if(event.key==='Escape'){event.preventDefault();closeVeilSelect(true)}else if(event.key==='Tab')closeVeilSelect()};select.addEventListener('change',()=>syncVeilSelect(select));new MutationObserver(()=>syncVeilSelect(select)).observe(select,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled','label','value']});syncVeilSelect(select)}
function enhanceVeilSelects(root=document){if(root.matches?.('select'))enhanceVeilSelect(root);root.querySelectorAll?.('select').forEach(enhanceVeilSelect)}
enhanceVeilSelects();new MutationObserver(records=>records.forEach(record=>record.addedNodes.forEach(node=>{if(node.nodeType===Node.ELEMENT_NODE)enhanceVeilSelects(node)}))).observe(document.body,{childList:true,subtree:true});
document.addEventListener('click',event=>{if(!veilSelectCurrent)return;const state=veilSelectState.get(veilSelectCurrent);if(!state.menu.contains(event.target)&&!state.trigger.contains(event.target))closeVeilSelect()});
window.addEventListener('resize',()=>closeVeilSelect());window.addEventListener('scroll',()=>closeVeilSelect(),true);

(()=>{ // custom avatar: stored on this PC, shared by every VEIL window
  const KEY='veil-avatar',apply=url=>document.querySelectorAll('img.peter-avatar').forEach(img=>{img.src=url||'assets/pep.png'});
  try{const saved=localStorage.getItem(KEY);if(saved)apply(saved)}catch{}
  const input=document.getElementById('avatar-file'),button=document.getElementById('avatar-change');
  if(!input||!button)return;
  button.onclick=()=>input.click();
  input.onchange=()=>{
    const file=input.files&&input.files[0];input.value='';if(!file)return;
    if(file.size>8*1024*1024){notify('头像超过 8MB');return}
    const image=new Image(),url=URL.createObjectURL(file);
    image.onload=()=>{
      if(image.width*image.height>40e6){URL.revokeObjectURL(url);notify('头像像素太多，换小一点的');return}
      const size=256,side=Math.min(image.width,image.height),canvas=document.createElement('canvas');canvas.width=canvas.height=size;
      canvas.getContext('2d').drawImage(image,(image.width-side)/2,(image.height-side)/2,side,side,0,0,size,size);
      URL.revokeObjectURL(url);const data=canvas.toDataURL('image/png');
      try{localStorage.setItem(KEY,data)}catch{}apply(data);
    };
    image.onerror=()=>URL.revokeObjectURL(url);image.src=url;
  };
  window.addEventListener('storage',event=>{if(event.key===KEY)apply(event.newValue)});
})();
