// Desktop IPC sends authenticated requests through the existing local Agent.
const $=id=>document.getElementById(id), col=$('col'), log=$('log'), box=$('box'), sendBtn=$('send');
const imageFile=$('image-file'),imageButton=$('image-button'),imagePreview=$('image-preview'),composer=$('composer');
const voiceHold=$('voice-hold');
const speechButton=$('speech-toggle'), speechStatus=$('speech-status');
const speechDraftKey='veil-speech-draft';
let speechRecorder=null,speechStream=null,speechChunks=[],speechSession=null,speechState='idle',speechPermissionPending=false,speechCancelled=false,speechDraftActive=false,speechTimer=null,speechErrorTimer=null;
let after=0, busy=true, transcribingVoice=false, stopping=false, stopped=false, typing=null, sending=false;
let revealQueue=[], revealTimer=null, roundStartedAt=0, roundHasReply=false, previousMessage=null;
let pendingApproval=null, activityEnabled=false, codexWatchEnabled=false, exiting=false;
const recentErrors=new Map();
let memoryAvailable=false,memoryLoaded=false,memoryLoading=false,memoryCursor='',memoryItems=[],memoryGroup='recent',memoryShown=50;
// Display-only groups over Ombre's own categories; Ombre's files keep their tags and categories.
const MEMORY_GROUPS={recent:{label:'最近',domains:null},us:{label:'我们俩',domains:['恋爱','人际']},peter:{label:'他自己',domains:['内心','心理','兴趣','游戏']},system:{label:'系统和能力',domains:['数字','AI','编程','工作']}};
let pendingQuote=null;
let quietEnabled=false;
let activityFilter='all',toolActivity=[],autonomyActivity=[],glanceActivity=[],detachedViews=new Set();
const activityRowNodes=new Map();
const pageQuery=new URLSearchParams(location.search),detachedView=pageQuery.get('detached')==='1'?pageQuery.get('view'):null;
let selectedImage=null;const awaitingImages=new Map();
let voiceRecorder=null,voiceStream=null,voiceChunks=[],voiceStarted=0,voiceTimer=null,voiceCancelled=false,voiceSending=false,voicePermissionPending=false;
const unsentVoiceBlobs=new Map();
let imageChoice=0;
const draftRequests=new Map();
const IMAGE_TYPES=new Set(['image/png','image/jpeg','image/gif','image/webp']);
const GROUP_GAP=15*60*1000;
function speechIsActive(){return speechState==='listening'||speechState==='processing'||!!speechRecorder||speechPermissionPending}
function notify(text,kind='notice'){
  const node=document.createElement('div');node.className=kind==='error'?'toast error':'toast';node.textContent=text;$('toasts').appendChild(node);
  setTimeout(()=>node.remove(),Math.min(15000,Math.max(2000,text.length*80)));
}
function setPage(name){
  if(!['chat','activity','context','memory','mcp','more'].includes(name))return;
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
  speechButton.textContent='T';
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
  setSpeechStatus('error','当前环境无法录音，请检查麦克风权限。');
}
function speechMime(){
  for(const mime of ['audio/webm;codecs=opus','audio/webm','audio/ogg;codecs=opus','audio/mp4'])if(MediaRecorder.isTypeSupported?.(mime))return mime;
  return '';
}
function stopSpeechTracks(){if(speechStream){speechStream.getTracks().forEach(track=>track.stop());speechStream=null}}
function stopSpeech(cancel=false){
  speechCancelled=speechCancelled||cancel;
  if(speechTimer){clearTimeout(speechTimer);speechTimer=null}
  if(speechRecorder&&!cancel)setSpeechStatus('processing','录音结束，正在转写…');
  if(speechRecorder&&speechRecorder.state!=='inactive')speechRecorder.stop();
  else if(speechPermissionPending){speechPermissionPending=false;speechCancelled=true;speechSession=null;setSpeechStatus('idle','已取消录音。')}
}
async function finishSpeech(){
  const recorder=speechRecorder;speechRecorder=null;stopSpeechTracks();
  if(speechTimer){clearTimeout(speechTimer);speechTimer=null}
  if(speechCancelled){speechCancelled=false;speechChunks=[];speechSession=null;setSpeechStatus('idle','已取消录音。');return}
  const mime=(recorder?.mimeType||speechChunks[0]?.type||'audio/webm').split(';')[0];
  const blob=new Blob(speechChunks,{type:mime});speechChunks=[];
  if(!blob.size){speechSession=null;setSpeechStatus('error','没有录到声音，请重试。');return}
  if(blob.size>10*1024*1024){speechSession=null;setSpeechStatus('error','录音超过 10 MB，请缩短后重试。');return}
  setSpeechStatus('processing','正在用 SenseVoice 转写…');
  try{
    const dataUrl=await fileAsDataUrl(blob),comma=dataUrl.indexOf(',');
    const r=await api('/api/transcribe',{mime,name:'dictation-'+Date.now(),data:dataUrl.slice(comma+1)},70000);
    const result=await r.json();
    if(!r.ok)throw new Error(result.transcription_error||'语音提交失败，请重试。');
    const transcript=(result.transcript||'').trim();
    if(!transcript)throw new Error(result.transcription_error||'SenseVoice 没有识别出文字，请重试。');
    renderSpeechText(transcript);speechSession=null;
    setSpeechStatus('idle','SenseVoice 识别完成，可编辑后发送。');
    box.focus();
  }catch(error){
    speechSession=null;setSpeechStatus('error',error.message||'SenseVoice 转写失败，请重试。');
  }
}
async function startSpeech(){
  if(speechRecorder){setSpeechStatus('processing','录音结束，正在转写…');speechRecorder.stop();return}
  if(speechPermissionPending||voiceRecorder||voicePermissionPending||voiceSending||busy||sending||stopping||stopped)return;
  speechCancelled=false;speechPermissionPending=true;speechSession={base:box.value};
  speechDraftActive=true;saveSpeechDraft(box.value);setSpeechStatus('processing','正在启动麦克风…');
  try{
    const stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:{ideal:1},sampleRate:{ideal:16000}}});speechPermissionPending=false;
    if(speechCancelled){stream.getTracks().forEach(track=>track.stop());return}
    speechStream=stream;speechChunks=[];
    const mime=speechMime();speechRecorder=new MediaRecorder(stream,{audioBitsPerSecond:48000,...(mime?{mimeType:mime}:{})});
    speechRecorder.ondataavailable=event=>{if(event.data?.size)speechChunks.push(event.data)};
    speechRecorder.onstop=finishSpeech;speechRecorder.start(500);
    speechTimer=setTimeout(()=>stopSpeech(false),120000);
    setSpeechStatus('listening','正在录音；再次点击结束并转写。');
  }catch(error){
    speechPermissionPending=false;speechSession=null;stopSpeechTracks();
    if(speechCancelled){speechCancelled=false;setSpeechStatus('idle','已取消录音。');return}
    setSpeechStatus('error','无法使用麦克风，请在 Windows 或桌面程序设置中允许后重试。');
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
      if(typeof e.text==='string'){node.dataset.messageText=e.text;node.replaceChildren();node.innerHTML=render(e.text)}
      updateFavoriteBadge(node,e.favorite===true);
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
  scrollIfFollowing(()=>add(e.kind,e.text,e.id,e.time,e.media,e.message_id,e.request_id,e.favorite,e.attachments));
  if(e.kind==='approval'&&e.id!==pendingApproval){
    const card=col.querySelector('.approval[data-id="'+e.id+'"]');
    card.classList.add('done');card.querySelector('.status').textContent='审批已结束，请看后续聊天提示。';
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
    showEvent(e);setBusy(busy);revealNext();
  };
  if(delay)revealTimer=setTimeout(reveal,delay);else reveal();
}
function receiveEvent(e,initial){
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
  if(media.localUrl)audio.src=media.localUrl;else window.veilDesktop.mediaUrl(media.filename).then(url=>{audio.src=url}).catch(()=>{});
  const updateDuration=()=>{if(Number.isFinite(audio.duration)&&audio.duration>0)clock.textContent=formatVoiceTime(audio.duration)};
  audio.onloadedmetadata=updateDuration;audio.ondurationchange=updateDuration;
  audio.onended=()=>{play.innerHTML=window.veilIcon('play',16);play.setAttribute('aria-label','播放语音');wrap.classList.remove('playing')};
  play.onclick=async()=>{if(audio.paused){try{await audio.play();play.innerHTML=window.veilIcon('pause',16);play.setAttribute('aria-label','暂停语音');wrap.classList.add('playing')}catch{status.textContent='无法播放这段语音'}}else{audio.pause();play.innerHTML=window.veilIcon('play',16);play.setAttribute('aria-label','播放语音');wrap.classList.remove('playing')}};
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
    window.veilDesktop.mediaUrl(attachment.filename).then(url=>{image.src=url}).catch(()=>{image.alt='图片附件无法读取'});
    card.appendChild(image);
  }else if(attachment.view==='html'&&attachment.mime==='text/html'){
    const open=document.createElement('button');open.type='button';open.className='msg-file-open';open.textContent='在浏览器中打开';
    open.onclick=async()=>{open.disabled=true;try{await window.veilDesktop.openAttachment(attachment.id)}catch(error){notify(error.message||'无法打开这个网页附件。','error')}finally{open.disabled=false}};
    card.appendChild(open);
  }else if(attachment.view==='text'&&attachment.mime==='text/plain'){
    const details=document.createElement('details');details.className='msg-file-text';
    const summary=document.createElement('summary');summary.textContent='在聊天窗口中查看';
    const content=document.createElement('pre');details.append(summary,content);
    let loaded=false,loading=false;
    details.addEventListener('toggle',async()=>{
      if(!details.open||loaded||loading)return;
      loading=true;content.textContent='正在读取…';
      try{content.textContent=await window.veilDesktop.attachmentText(attachment.id);loaded=true}
      catch(error){content.textContent=error.message||'无法读取这个文本附件。'}
      finally{loading=false}
    });
    card.appendChild(details);
  }else{
    const unsupported=document.createElement('span');unsupported.className='msg-file-note';unsupported.textContent='此文件类型暂不支持预览';card.appendChild(unsupported);
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
function add(kind,text,id,time,media,messageId,requestId,favorite=false,attachments=[]){
  if(kind==='error'){
    const previous=recentErrors.get(text),now=Date.now();
    if(previous&&now-previous.at<30000){previous.count++;previous.at=now;previous.node.textContent=text+'（重复 '+previous.count+' 次）';return}
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
      const image=document.createElement('img');image.className='msg-image';window.veilDesktop.mediaUrl(media.filename).then(url=>{image.src=url});image.alt=media.name||'图片';image.loading='lazy';d.appendChild(image);
      if(text&&text!=='[图片]'){const caption=document.createElement('div');caption.innerHTML=render(text);d.appendChild(caption)}
    }else d.innerHTML=render(text);
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
  const status=document.createElement('p');status.textContent='正在读取这条回复使用的记忆…';content.appendChild(status);
  const buttons=document.createElement('div'),closeButton=document.createElement('button');closeButton.type='button';closeButton.textContent='Close';
  const close=()=>{layer.remove();node.focus({preventScroll:true})};backdrop.onclick=closeButton.onclick=close;
  dialog.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();close()}});
  buttons.append(closeButton);dialog.append(title,explanation,content,buttons);layer.append(backdrop,dialog);document.body.appendChild(layer);closeButton.focus();
  try{
    const result=await api('/api/message/action',{action:'memory',message_id:node.dataset.messageId});
    const data=await result.json();if(!result.ok)throw new Error(data.message||'无法读取这条回复的记忆引用。');
    if(!Array.isArray(data.items)||!data.items.length){status.textContent='这条回复没有自动提供的 Ombre 记忆。可在“记忆”页面查看和管理独立记忆。';return}
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
          if(!pageResponse.ok)throw new Error(page.error||'这条记忆暂时无法读取。');
          if(typeof page.text!=='string'||typeof page.version!=='string')throw new Error('记忆内容格式无法识别。');
          version=page.version;text+=page.text;offset=page.next_offset;
        }while(offset!==null);
        const memory=memoryDisplay(text);heading.textContent=[memory.date,...memory.tags].filter(Boolean).join(' · ')||'Memory';
        body.textContent=memory.body||'（空记忆）';
      }catch(error){body.textContent=error.message||'这条记忆暂时无法读取。'}
    }
    if(data.items.length>5){const note=document.createElement('p');note.textContent=`仅显示前 5 条，共 ${data.items.length} 条。`;content.appendChild(note)}
  }catch(error){status.textContent=error.message||'无法读取这条回复的记忆引用。'}
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
    ['delete','Delete','trash',false],...(user?[['recall','Recall','undo',!recallAllowed(node)]]:[])
  ]:[
    ['copy','Copy','copy',!(node.dataset.messageText||'').trim()||!!node.dataset.mediaFilename],
    ['edit','Edit','pencil',!((node.dataset.messageText||'').trim())||!!node.dataset.mediaFilename],
    ['quote','Quote','quote',!(node.dataset.messageText||'').trim()],
    ['memory','Memory','brain',user],['select','Select','select',false],
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
        navigator.clipboard.writeText(content).then(()=>notify('消息已复制。')).catch(()=>notify('无法复制这条消息。','error'));
        return;
      }
      if(action==='transcribe'){
        const text=node.querySelector('.msg-voice .voice-transcript');
        if(text)text.hidden=!text.hidden;
        closeMessageActions();return;
      }
      if(action==='quote'){closeMessageActions();quoteMessage(node);return}
      if(action==='memory'){closeMessageActions();showMessageMemory(node);return}
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
  if(waiting&&typing){typing.firstChild.textContent=transcribingVoice?'正在转写语音…':($('title').textContent||'')+' 正在回复…';const stopButton=typing.querySelector('button');stopButton.hidden=!b||transcribingVoice;if(!b)stopButton.disabled=false}
  if((!waiting||stopped)&&typing){typing.remove();typing=null}
}
// Ticket 138: Lan can stop a reply that keeps going; Peter stops before his next step.
async function stopTurn(e){
  const button=e.currentTarget;button.disabled=true;
  try{const r=await api('/api/stop',{});if(!r.ok)button.disabled=false}catch(err){button.disabled=false}
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
function renderMemoryList(){
  const list=$('memory-list'),query=$('memory-search').value.trim().toLocaleLowerCase(),group=MEMORY_GROUPS[memoryGroup];list.replaceChildren();
  $('memory-heading').textContent=group.label;
  const filtered=memoryItems.filter(item=>!item.source_record&&(!group.domains||item.domain.some(name=>group.domains.includes(name)))&&memoryMatches(item,query));
  if(!filtered.length){const empty=document.createElement('p');empty.className='muted';empty.textContent=query?'没有匹配的记忆。':'这里还没有记忆。';list.appendChild(empty)}
  let lastDay='';
  for(const item of filtered.slice(0,memoryShown)){
    const day=memoryDay(item.created);
    if(day!==lastDay){const heading=document.createElement('h4');heading.className='memory-day';heading.textContent=day;list.appendChild(heading);lastDay=day}
    const button=document.createElement('button');button.type='button';button.className='memory-entry';
    const top=document.createElement('span');top.className='memory-entry-top';
    const title=document.createElement('span');title.className='memory-entry-title';title.textContent=item.surprise?'🎁 '+item.title:item.title;
    const time=document.createElement('time');time.textContent=memoryTime(item.created);top.append(title,time);
    const preview=document.createElement('span');preview.className='memory-entry-preview';
    preview.textContent=item.surprise?'Peter 给你准备的惊喜，先不剧透。':(item.body||item.why).replace(/\s+/g,' ').slice(0,90);
    button.append(top,preview);
    button.setAttribute('aria-current',String($('memory-detail').dataset.memoryId===item.memory_id));
    button.onclick=()=>readMemory(item);list.appendChild(button);
  }
  if(filtered.length>memoryShown){const more=document.createElement('button');more.type='button';more.className='memory-entry more';more.textContent=`看更早的（还有 ${filtered.length-memoryShown} 条）`;more.onclick=()=>{memoryShown+=50;renderMemoryList()};list.appendChild(more)}
  const total=memoryItems.filter(item=>!item.source_record).length;
  $('memory-count').textContent=group.domains||query?`显示 ${filtered.length} 条 · 共 ${total} 条记忆`:`共 ${total} 条记忆`;
}
async function loadMemoryCatalog(reset=false){
  if(memoryLoading)return;
  memoryLoading=true;memoryCursor='';
  if(reset){memoryLoaded=false;memoryItems=[];$('memory-list').textContent='正在读取 Ombre 记忆…'}
  try{
    const r=await api('/api/memory/panel');const data=await r.json();
    if(!r.ok)throw new Error(data.error||'Ombre 记忆暂时无法读取。');
    if(!data||!Array.isArray(data.items))throw new Error('Ombre 返回的记忆格式无法识别。');
    memoryItems=data.items.filter(item=>item&&typeof item.memory_id==='string'&&typeof item.title==='string')
      .map(item=>({...item,domain:Array.isArray(item.domain)?item.domain:[],tags:Array.isArray(item.tags)?item.tags:[]}));
    memoryLoaded=true;memoryAvailable=true;renderMemoryList();
    const current=memoryItems.find(item=>item.memory_id===$('memory-detail').dataset.memoryId);if(current)readMemory(current);
  }catch(error){
    memoryLoaded=true;$('memory-list').textContent=error.message||'Ombre 记忆暂时无法读取。';$('memory-unavailable').hidden=false;
    $('memory-count').textContent='读取失败';
  }finally{memoryLoading=false}
}
async function readRawMemory(memoryId){
  let offset=0,version='',content='';
  do{
    const params=new URLSearchParams({id:memoryId,offset:String(offset)});if(version)params.set('version',version);
    const r=await api('/api/memory/read?'+params.toString());const page=await r.json();
    if(!r.ok)throw new Error(page.error||'这条记忆暂时无法读取。');
    if(typeof page.text!=='string'||typeof page.version!=='string')throw new Error('Ombre 返回的记忆内容格式无法识别。');
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
    const note=document.createElement('p');note.className='memory-surprise';note.textContent='这条是 Peter 给你准备的惊喜，内容先藏起来了，不能在这里编辑。';
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
      delete detail.dataset.memoryId;const done=document.createElement('p');done.className='muted';done.textContent='已删除「'+item.title+'」。删除后不能恢复。';
      detail.replaceChildren(done);await loadMemoryCatalog(false);
    }catch(error){status.textContent=error.message||'没能删除。';remove.disabled=false;remove.textContent='删除这条';remove.classList.remove('armed')}
  };
  const raw=document.createElement('details');raw.className='memory-raw';
  const summary=document.createElement('summary');summary.textContent='原始文件（含全部字段和标签）';const pre=document.createElement('pre');raw.append(summary,pre);
  raw.ontoggle=async()=>{if(!raw.open||pre.dataset.loaded)return;pre.textContent='正在读取…';
    try{pre.textContent=await readRawMemory(item.memory_id);pre.dataset.loaded='true'}catch(error){pre.textContent=error.message||'这条记忆暂时无法读取。'}};
  const hint=document.createElement('p');hint.className='memory-hint';hint.textContent='保存会把旧版本留档；删除不能恢复。Peter 不会收到通知。';
  detail.replaceChildren(eyebrow,heading,facts,title.wrap,why.wrap,body.wrap,actions,status,hint,raw);
}
async function approve(card,status,answer,id){
  if(card.dataset.sending==='true'||card.classList.contains('done'))return;
  card.dataset.sending='true';
  card.querySelectorAll('button').forEach(button=>button.disabled=true);
  status.textContent='正在提交…';
  try{
    const r=await api('/api/approve',{answer,id});
    if(!r.ok){
      if(r.status===409){card.classList.add('done');status.textContent='这项审批已失效，未重复执行。'}
      else throw new Error(r.status);
      return;
    }
    card.classList.add('done');
    status.textContent=answer==='y'?'服务器已收到同意。':'服务器已收到拒绝。';
  }catch(e){
    status.textContent='没有收到服务器确认；正在核对审批状态。';
    try{
      const r=await api('/api/events?after='+after),s=await r.json();
      if(s.pending===Number(id)){
        card.querySelectorAll('button').forEach(button=>button.disabled=false);
        status.textContent='服务器仍在等待选择，可以重新提交。';
      }else{
        card.classList.add('done');
        status.textContent='页面没有收到确认；请看后续聊天提示。';
      }
    }catch(_){
      card.querySelectorAll('button').forEach(button=>button.disabled=false);
      status.textContent='暂时无法确认；审批仍可重试，重复操作会被服务器拒绝。';
    }
  }finally{card.dataset.sending='false'}
}
function activityDetails(record){
  const lines=[record.started_at+' → '+record.finished_at,
    '结果：'+record.result+'；'+record.result_reason,
    '决定/结束信息：'+record.decision,
    '已留存文件：'+((record.lab_files_changed||[]).join('、')||'无')];
  for(const item of record.tools||[])lines.push(item.tool+' | '+item.path+' | '+item.state+
    (item.result_state==='结果过长'?'；结果过长，未提供给模型':''));
  if(record.current_focus)lines.push('当时关注：'+record.current_focus);
  if(record.next_step)lines.push('下一步：'+record.next_step);
  lines.push('本次费用：¥'+record.cost_cny);
  return lines.join('\n');
}
const TOOL_LABELS={read_text_file:'读取文件',read_multiple_files:'读取多个文件',write_file:'写入文件',append_file:'追加文件',list_directory:'查看文件夹',directory_tree:'查看目录树',search_files:'查找文件',get_file_info:'查看文件信息',create_directory:'创建文件夹',move_lab_file:'移动 Lab 文件',lab_file_digest:'核对 Lab 文件',hold:'保存记忆',breath_search:'查找记忆',trace:'更新记忆',dream:'整理记忆',letter_write:'写记忆信件',letter_read:'读取记忆信件',memory_history:'查看记忆历史',memory_catalog:'查看记忆目录',memory_read:'读取记忆',long_task_status:'查看长期任务',long_task_update:'更新长期任务',web_search:'联网搜索',web_fetch:'读取网页',send_message:'发送消息',screen_pop:'屏幕弹出'};
function activityStatus(state){
  if(state==='进行中')return {text:'进行中',className:'running'};
  if(state==='成功')return {text:'成功',className:'success'};
  if(state==='被拒绝')return {text:'权限拒绝',className:'failure'};
  if(state==='出错'||state==='失败')return {text:'失败',className:'failure'};
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
    if(label==='执行结果'){values.result=document.createElement('pre');dd.appendChild(values.result)}else values[label]=dd;
    dl.append(dt,dd);
  }
  body.appendChild(dl);details.append(summary,body);
  return {details,time,name,status,code,source,values};
}
function updateActivityRow(row,item){
  row.time.textContent=activityTime(item.started_at||item.finished_at);
  row.name.textContent=TOOL_LABELS[item.tool]||item.tool;
  const screenPopOpen=['还开着','弹幕播放中'].includes(item.screen_pop_state);
  const state=item.screen_pop_state
    ? {text:item.screen_pop_state,className:screenPopOpen?'running':'success'}
    : activityStatus(item.state);
  row.status.className='activity-state '+state.className;row.status.textContent=state.text;
  row.code.textContent=item.tool;row.source.textContent='来源：'+(item.source||'聊天');
  row.values['操作对象'].textContent=item.path||'未提供';
  row.values['开始时间'].textContent=item.started_at?new Date(item.started_at).toLocaleString():'旧记录未保存开始时间';
  row.values['完成时间'].textContent=item.finished_at?new Date(item.finished_at).toLocaleString():item.state==='进行中'?'尚未完成':'旧记录未保存完成时间';
  row.values.result.textContent=String(item.detail||state.text);
}
function renderActivityTimeline(){
  const all=combinedActivity();
  const visible=all.filter(item=>activityFilter==='all'||item.kind===activityFilter||(activityFilter==='autonomy'&&item.source==='自主活动'));
  const list=$('activity-timeline');$('activity-count').textContent=all.length+' 条记录';
  const running=toolActivity.filter(item=>item.state==='进行中').length;
  $('activity-work-status').textContent=running?`正在执行 ${running} 项工具调用`:'等待下一次工具调用';
  $('activity-work-dot').className='status-dot '+(running?'is-busy':'is-online');
  const present=new Set(all.map(item=>String(item.call_id||`${item.tool}:${item.started_at||item.finished_at||''}`)));
  for(const [key,row] of activityRowNodes){if(!present.has(key)){row.details.remove();activityRowNodes.delete(key)}}
  const visibleKeys=new Set(visible.map(item=>String(item.call_id||`${item.tool}:${item.started_at||item.finished_at||''}`)));
  for(const node of [...list.children])if(!visibleKeys.has(node.dataset.activityCallId||''))node.remove();
  let empty=list.querySelector('[data-activity-empty]');
  if(!visible.length){
    if(!empty){empty=document.createElement('p');empty.dataset.activityEmpty='true';empty.className='muted';list.appendChild(empty)}
    empty.textContent=all.length?'此筛选下还没有记录。':'还没有工具或自主活动记录。';return;
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
function renderLiveContext(live){
  live=live||{};const key=JSON.stringify(live);if(key===liveContextKey)return;liveContextKey=key;
  const turns=Array.isArray(live.turns)?live.turns:[],turnList=$('context-turns');turnList.replaceChildren();
  if(!turns.length){const p=document.createElement('p');p.className='muted';p.textContent='还没有记录。Peter 回复一次后这里就会出现。';turnList.appendChild(p)}
  for(const turn of turns){
    const tools=Array.isArray(turn.tools)?turn.tools:[];
    const counts={};for(const item of tools){const name=item.tool||'?';counts[name]=(counts[name]||0)+1}
    const toolText=Object.entries(counts).map(([name,count])=>count>1?`${name} ×${count}`:name).join('、');
    turnList.appendChild(contextRow({head:[['time','',shortTime(turn.time)],['strong','',CONTEXT_KINDS[turn.kind]||turn.kind||'请求']],
      lines:[['meta',`原文 ${turn.history_rounds??0} 轮 · 背景 ${turn.background_count||0} 段 · 长期记忆 ${turn.memory_count||0} 条 · 便签 ${turn.notes_count||0} 条`+(turn.estimated_input_tokens?` · 约 ${turn.estimated_input_tokens} tokens`:'')],
        ['meta',toolText?'工具：'+toolText:'']]}));
  }
  const notes=live.notes,noteList=$('context-notes');noteList.replaceChildren();
  if(!notes){$('context-notes-next').textContent='';const p=document.createElement('p');p.className='muted';p.textContent='短期便签没有开启。';noteList.appendChild(p);return}
  const next=shortTime(notes.next_extraction_at);$('context-notes-next').textContent=next?`下次整理便签约 ${next}`:'';
  const items=[...(notes.active||[]),...(notes.recent||[])];
  if(!items.length){const p=document.createElement('p');p.className='muted';p.textContent='还没有便签。聊天每 30 分钟整理一次，点"新对话"前也会整理。';noteList.appendChild(p)}
  for(const note of items){
    const [label,cls]=NOTE_STATUS[note.status]||[note.status||'',''];
    const fades=note.status==='进行中'&&shortTime(note.fades_at)?`约 ${shortTime(note.fades_at)} 无进展就淡出`:'';
    const changed=note.status!=='进行中'&&shortTime(note.changed_at)?`${label}于 ${shortTime(note.changed_at)}`:'';
    noteList.appendChild(contextRow({head:[['time','',shortTime(note.created_at)],['span','note-status '+cls,label]],
      lines:[['note-text',note.text||''],['meta',[note.updated_at&&note.updated_at!==note.created_at?`更新 ${shortTime(note.updated_at)}`:'',fades||changed].filter(Boolean).join(' · ')]]}));
  }
}
async function poll(){
  try{
    const r=await api('/api/events?after='+after);
    if(!r.ok)throw new Error(r.status);
    const s=await r.json();
    $('offline').style.display='none';
    if(detachedView==='mcp'){
      $('ended').style.display=s.stopped?'block':'none';
      if(s.events?.length)after=s.events[s.events.length-1].id;
      setTimeout(poll,600);return;
    }
    stopping=s.stopping===true&&!s.stopped;stopped=s.stopped===true;
    $('ended').style.display=stopped?'block':'none';
    $('service-status').textContent=stopped?'服务：已停止':stopping?'服务：正在退出':'服务：运行中';
    $('service-status-detail').textContent='服务运行状态：'+(stopped?'已停止':stopping?'退出请求已收到，正在保存并关闭':'运行中');
    const runtime=s.runtime_status||{};
    $('runtime-status').textContent=runtime.message||$('service-status').textContent;
    $('runtime-details').hidden=!runtime.state||runtime.state==='healthy';
    $('runtime-detail-text').textContent=runtime.state&&runtime.state!=='healthy'
      ?[runtime.time,runtime.module+' · '+runtime.classification+' · '+runtime.code,runtime.action].filter(Boolean).join('\n')
      :'';
    activityEnabled=s.activity_enabled===true;
    const nextDate=s.next_wake_at?new Date(s.next_wake_at):null;
    const next=nextDate&&Number.isFinite(nextDate.getTime())?nextDate.toLocaleString():'';
    $('next-wake').textContent=next||s.next_wake_reason||'Agent 暂未提供确定的唤醒时间。';
    $('next-wake-reason').textContent=next?'Agent 已排定的下次自主活动时间。':'';
    const state=!activityEnabled?'已暂停':(s.autonomy_busy?'正在活动':'已开启');
    $('header-autonomy-status').textContent=state;
    $('autonomy-indicator').className='status-dot '+(!activityEnabled?'is-offline':s.autonomy_busy?'is-busy':'is-online');
    $('header-next-wake').textContent=nextDate&&Number.isFinite(nextDate.getTime())
      ?nextDate.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}):'未确定';
    $('header-next-wake').parentElement.title=next||s.next_wake_reason||'Agent 暂未提供确定的唤醒时间。';
    $('activity-state').textContent=state;
    $('activity-status').textContent='自主活动'+state;
    renderActivity(s.last_activity,s.activity_history||[],s.tool_activity||[],s.glance_activity||[]);
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
    $('glance-status').textContent=!glanceEnabled?'已暂停':!activityEnabled?'全局自主活动暂停中':s.autonomy_busy?'正在瞄一眼':'已开启（每 15–40 分钟）';
    const glanceNext=s.glance_next_at?new Date(s.glance_next_at):null;
    $('glance-next').textContent=glanceNext&&Number.isFinite(glanceNext.getTime())?glanceNext.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}):(!glanceEnabled?'未排定':!activityEnabled?'恢复自主活动后重新计时':'等待电脑前活动');
    $('glance-toggle').textContent=glanceEnabled?'暂停瞄一眼':'开启瞄一眼';
    $('glance-toggle').disabled=stopping||stopped||exiting;
    codexWatchEnabled=s.codex_watch_enabled===true;
    $('codex-watch-status').textContent=!codexWatchEnabled?'已暂停':
      (s.codex_watch_available==='没有找到 Codex 会话文件'?s.codex_watch_available:(s.codex_watch_status||'正在同步'));
    $('codex-watch-toggle').textContent=codexWatchEnabled?'暂停监视':'开启监视';
    $('codex-watch-toggle').disabled=stopping||stopped||exiting;
    $('exit-service').disabled=stopping||stopped||exiting;
    document.title=s.name;$('title').textContent=s.name;
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
    else if(!memoryAvailable&&!memoryLoaded){$('memory-list').textContent='当前 Agent 未提供 Ombre Memory 数据。';$('memory-count').textContent='不可用'}
    const stick=log.scrollHeight-log.scrollTop-log.clientHeight<80;
    const initial=after===0;pendingApproval=s.pending;
    for(const e of s.events){receiveEvent(e,initial);after=e.id}
    // Only the approval being asked right now can be clicked; older cards are inert.
    document.querySelectorAll('.approval').forEach(c=>{
      if(Number(c.dataset.id)!==s.pending){
        c.classList.add('done');
        const status=c.querySelector('.status');
        if(status&&!status.textContent)status.textContent='审批已结束，请看后续聊天提示。';
      }
    });
    transcribingVoice=s.transcribing_voice===true;setBusy(s.busy);
    if(s.events.length&&stick)log.scrollTop=log.scrollHeight;
  }catch(e){$('offline').style.display='block';$('service-status').textContent='服务：连接中断';$('service-status-detail').textContent='服务运行状态：连接中断';$('activity-toggle').disabled=true;$('exit-service').disabled=true}
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
  if(!mime){if(choice===imageChoice)add('error','只支持 PNG、JPEG、GIF 或 WebP 图片。');return}
  let prepared;
  try{prepared=await prepareImage(file,mime)}catch(e){if(choice===imageChoice)add('error',e.message==='source too large'?'原图超过 25 MB，暂时无法处理。':'图片读取或压缩失败，请换一张图片重试。');return}
  if(choice!==imageChoice)return;
  clearImage(false);
  selectedImage={file:prepared,url:URL.createObjectURL(prepared),media:null,mime:prepared.type,choice,key:crypto.randomUUID()};
  const img=document.createElement('img');img.src=selectedImage.url;img.alt='待发送图片';imagePreview.appendChild(img);
  const label=document.createElement('span');label.textContent=file.name||'图片';imagePreview.appendChild(label);
  const cancel=document.createElement('button');cancel.type='button';cancel.textContent='取消';cancel.onclick=clearImage;selectedImage.cancel=cancel;imagePreview.appendChild(cancel);imagePreview.hidden=false;
}
imageButton.onclick=()=>imageFile.click();
imageFile.onchange=()=>chooseImage(imageFile.files?.[0]);
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
function resetVoiceButton(){clearInterval(voiceTimer);voiceTimer=null;voiceHold.dataset.recording='false';voiceHold.setAttribute('aria-pressed','false');voiceHold.setAttribute('aria-label','点击开始录音');voiceHold.title='点击开始录音';voiceHold.innerHTML=window.veilIcon('mic',18);$('voice-record-status').hidden=true;$('voice-record-cancel').hidden=true}
async function startVoice(){
  if(voiceRecorder||voicePermissionPending||voiceSending||busy||sending||stopping||stopped||speechIsActive())return;
  if(!navigator.mediaDevices?.getUserMedia||typeof MediaRecorder==='undefined'){add('error','当前环境不支持录音。');return}
  voiceCancelled=false;voicePermissionPending=true;voiceChunks=[];setBusy(busy);
  try{
    const stream=await navigator.mediaDevices.getUserMedia({audio:true});voicePermissionPending=false;
    if(voiceCancelled){stream.getTracks().forEach(track=>track.stop());voiceCancelled=false;resetVoiceButton();setBusy(busy);return}
    voiceStream=stream;
    const mime=voiceMime();voiceRecorder=new MediaRecorder(voiceStream,mime?{mimeType:mime}:undefined);voiceStarted=Date.now();
    voiceRecorder.ondataavailable=e=>{if(e.data?.size)voiceChunks.push(e.data)};
    voiceRecorder.onstop=finishVoice;
    voiceRecorder.start(500);voiceHold.dataset.recording='true';voiceHold.setAttribute('aria-pressed','true');voiceHold.setAttribute('aria-label','点击结束录音并发送');voiceHold.title='点击结束录音并发送';$('voice-record-status').hidden=false;$('voice-record-cancel').hidden=false;$('voice-record-label').textContent='录音中 0:00 · 再点一次发送';setBusy(busy);
    voiceTimer=setInterval(()=>{const seconds=Math.floor((Date.now()-voiceStarted)/1000);$('voice-record-label').textContent='录音中 '+Math.floor(seconds/60)+':'+String(seconds%60).padStart(2,'0')+' · 再点一次发送';if(seconds>=120)stopVoice(false)},250);
  }catch(e){voicePermissionPending=false;stopVoiceTracks();voiceRecorder=null;resetVoiceButton();setBusy(busy);if(!voiceCancelled)add('error','无法使用麦克风，请在系统或浏览器设置中允许后重试。');voiceCancelled=false}
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
  if(voiceCancelled){voiceCancelled=false;setBusy(busy);add('notice','已取消这条录音。');return}
  const mime=(recorder.mimeType||voiceChunks[0]?.type||'audio/webm').split(';')[0];
  const blob=new Blob(voiceChunks,{type:mime});voiceChunks=[];
  if(!blob.size){setBusy(busy);add('error','没有录到声音，请重试。');return}
  if(blob.size>10*1024*1024){setBusy(busy);add('error','录音超过 10 MB，未保存；请缩短后重试。');return}
  voiceSending=true;setBusy(busy);voiceHold.title='正在发送…';
  let savedMedia=null;
  try{
    savedMedia=await uploadVoice(blob,mime);
    savedMedia.transcription_pending=true;
    add('user','[voice message]',undefined,new Date().toISOString(),savedMedia);
    try{await sendVoice(savedMedia)}catch(error){if(error.media)Object.assign(savedMedia,error.media);if(!savedMedia.delivery_error)savedMedia.delivery_error='语音已保存，但暂时没有交给 Peter；可以重试发送。';updateVoiceMessage([...col.querySelectorAll('.msg')].find(node=>node.dataset.mediaId===savedMedia.id),savedMedia)}
  }catch(e){
    const id='local-'+crypto.randomUUID(),localUrl=URL.createObjectURL(blob);
    savedMedia={id,filename:'voice.webm',mime,kind:'voice',localUrl,transcription_pending:false,delivery_error:'语音暂时没有保存；录音仍保留，可以重试发送。'};
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
    else{Object.assign(media,result.media||{});media.transcription_error=result.transcription_error||'没有识别出文字，可以重试。';media.transcription_pending=false;updateVoiceMessage(wrap.closest('.msg'),media);button.disabled=false}
  }catch(e){if(e.media)Object.assign(media,e.media);else{media.transcription_error=media.transcript?'发送结果未确认；录音仍保留，可以重试发送。':'重试失败，录音仍保留；可以重试识别。';media.transcription_pending=false}updateVoiceMessage(wrap.closest('.msg'),media);button.disabled=false}
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
  if(attachedImage&&text.startsWith('/')){add('error','图片不能和命令一起发送；图片仍保留在发送区。');return}
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
      add('error',r.status===409&&result.error==='busy'
        ?'Peter 正在处理上一条消息；这条没有发送，输入已保留。'
        :'发送未成功，输入已保留；可以稍后重新操作。');
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
      if(result.duplicate)add('notice','这条消息此前已提交，没有重复发送。');
    }
  }catch(e){add('error',attachedImage?'图片发送未确认，图片仍在发送区；请稍后重试。':'发送结果未确认，输入已保留；页面不会自动重发。')}
  finally{
    if(attachedImage?.cancel&&selectedImage===attachedImage)attachedImage.cancel.disabled=false;
    sending=false;log.scrollTop=log.scrollHeight
  }
}
$('new-chat').onclick=()=>{
  if(busy||sending||stopping||stopped||revealQueue.length||speechIsActive())return;
  if(confirm('开始新对话？旧聊天仍保存在本机，记忆和实验室文件不会清除。'))send('/new');
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
  if(!r.ok)add('error','请求回复没有提交成功，请稍后重试。');
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
  }catch(e){$('activity-status').textContent='状态保存失败：请检查本机 Agent 连接后重试。';add('error','自主活动状态保存失败，请检查连接后重试。')}
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
  }catch(e){$('quiet-status').textContent='设置保存失败：请检查本机 Agent 连接后重试。';add('error','夜间安静设置保存失败，请检查连接后重试。')}
  finally{$('quiet-toggle').disabled=stopping||stopped||exiting}
};
$('glance-toggle').onclick=async()=>{
  $('glance-toggle').disabled=true;
  try{
    const enabled=$('glance-toggle').textContent==='开启瞄一眼';
    const r=await api('/api/glance',{enabled});
    if(!r.ok)throw new Error(r.status);
  }catch(e){$('glance-status').textContent='设置保存失败：请检查本机 Agent 连接后重试。';add('error','瞄一眼设置保存失败，请检查连接后重试。')}
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
  }catch(e){$('codex-watch-status').textContent='设置保存失败：请检查本机 Agent 连接后重试。';add('error','Codex 监视设置保存失败，请检查连接后重试。')}
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
    $('service-status').textContent='服务：退出状态未确认';
    add('error','没有收到退出确认；页面会继续核对服务状态，请不要重复启动。');
  }
};
$('reconnect').onclick=async()=>{
  const button=$('reconnect');button.disabled=true;button.textContent='正在重新连接…';
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
  appearance.theme=button.dataset.themeChoice;applyAppearance();storeAppearance();window.veilDesktop.saveTheme(appearance.theme);
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
  $('background-name').textContent=name||'尚未选择图片';$('clear-background').disabled=!dataUrl;
}
window.veilDesktop.getBackground().then(dataUrl=>setBackground(dataUrl,dataUrl?'已保存的背景图片':'' )).catch(()=>setBackground(null,''));
$('choose-background').onclick=async()=>{try{const result=await window.veilDesktop.chooseBackground();if(result){setBackground(result.dataUrl,result.name);notify('背景图片已保存。')}}catch(error){notify(error.message||'背景图片读取失败。','error')}};
$('clear-background').onclick=async()=>{if(await window.veilDesktop.clearBackground()){setBackground(null,'');notify('背景图片已移除。')}};
function applyCustomCSS(value){
  const css=String(value||'');
  if(css.length>20000||/@import\b|url\s*\(|expression\s*\(|-moz-binding|behavior\s*:/i.test(css))throw new Error('自定义 CSS 不能加载外部资源或脚本，也不能超过 20,000 个字符。');
  $('custom-style').textContent=css;
}
try{$('custom-css').value=localStorage.getItem(`${APPEARANCE_KEY}-css`)||'';applyCustomCSS($('custom-css').value)}catch{$('custom-css').value=''}
$('apply-css').onclick=()=>{try{applyCustomCSS($('custom-css').value);localStorage.setItem(`${APPEARANCE_KEY}-css`,$('custom-css').value);notify('自定义 CSS 已应用并保存。')}catch(error){notify(error.message,'error')}};
$('reset-appearance').onclick=async()=>{appearance={...appearanceDefaults,pageOpacity:{...appearanceDefaults.pageOpacity}};applyAppearance();storeAppearance();window.veilDesktop.setPetSize(appearance.petSize);$('custom-css').value='';$('custom-style').textContent='';localStorage.removeItem(`${APPEARANCE_KEY}-css`);await window.veilDesktop.clearBackground();setBackground(null,'');notify('外观已恢复默认设置。')};
$('memory-search').addEventListener('input',()=>{memoryShown=50;renderMemoryList()});
document.querySelectorAll('[data-memory-group]').forEach(button=>button.onclick=()=>{
  memoryGroup=button.dataset.memoryGroup;memoryShown=50;
  document.querySelectorAll('[data-memory-group]').forEach(other=>other.classList.toggle('active',other===button));renderMemoryList();
});
$('memory-reload').onclick=()=>{memoryItems=[];memoryCursor='';memoryLoaded=false;loadMemoryCatalog(true)};
document.querySelectorAll('[data-page]').forEach(button=>button.onclick=()=>{
  if(detachedViews.has(button.dataset.page)){window.veilDesktop.focusView(button.dataset.page);return}
  setPage(button.dataset.page);
  if(button.dataset.page==='memory'&&memoryAvailable&&!memoryLoaded&&!memoryLoading)loadMemoryCatalog(true);
  if(button.dataset.page==='mcp')loadMCP();
});
$('memory-panel').onclick=()=>window.veilDesktop.openMemory();
document.querySelectorAll('[data-cmd="/cost"]').forEach(button=>button.onclick=()=>{setPage('chat');send('/cost')});
$('memory-panel').title='在 Ombre Brain 中打开高级记忆管理';
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
window.veilDesktop.onShowView(view=>setPage(view));
window.veilDesktop.onShowSettings(()=>setPage('more'));
if(detachedView&&['chat','activity','context','memory','mcp','more'].includes(detachedView)){
  document.body.classList.add('detached-window');$('detached-header').hidden=false;
  $('detached-title').textContent='Peter · '+({chat:'聊天',activity:'活动记录',context:'实时上下文',memory:'记忆',mcp:'MCP',more:'更多'}[detachedView]);setPage(detachedView);
}else{setPage('chat');box.focus()}
setBusy(true);poll();

let mcpView={enabled:false,services:[]},mcpEditing=null,mcpLoading=false,mcpReady=false,mcpSaving=false,mcpSaveQueue=Promise.resolve(),mcpGeneration=0,mcpSignature='',mcpActionError='';
$('mcp-add').disabled=true;$('mcp-enabled').disabled=true;
function mcpConfig(source=mcpView){return {enabled:source.enabled,services:source.services.map(({id,builtin,command,args,cwd,enabled,permission})=>builtin?{id,builtin:true,enabled}:{id,command,args,cwd,enabled,permission})}}
async function loadMCP(force=false){
  if(mcpLoading||mcpSaving)return;
  const generation=mcpGeneration;
  mcpLoading=true;if(!mcpReady||force)$('mcp-status').textContent='正在读取服务状态…';
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
  if(!mcpReady){$('mcp-status').textContent='请先等服务配置读取完成。';return false}
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
    throw new Error('配置正在其他窗口修改，请稍后重试。');
  }
  catch(error){mcpActionError=error.message;$('mcp-status').textContent=error.message;return false}
  finally{mcpSaving=false;mcpGeneration++}
}
function renderMCP(){
  $('mcp-add').disabled=!mcpReady;$('mcp-enabled').disabled=!mcpReady;
  $('mcp-enabled').checked=mcpView.enabled;
  $('mcp-status').textContent=mcpActionError||mcpView.error||`${mcpView.services.length} 个服务`;
  const list=$('mcp-list');list.replaceChildren();
  if(!mcpView.services.length){const p=document.createElement('p');p.className='muted';p.textContent='还没有本地 MCP 服务。';list.append(p)}
  for(const service of mcpView.services){
    const card=document.createElement('article');card.className='surface mcp-card';
    const heading=document.createElement('div');heading.className='section-heading';
    const title=document.createElement('h3');title.textContent=service.id;
    const status=document.createElement('span');status.className='state-pill';status.textContent=service.builtin?(service.enabled&&mcpView.enabled?'已启用':'已停用'):service.status==='connected'?'已连接':service.status==='connecting'?'连接中':service.status==='error'?'连接失败':'已停用';
    heading.append(title,status);card.append(heading);
    const details=document.createElement('p');details.textContent=service.builtin?`${service.tools.length} 个工具 · VEIL 内置集成`:`${service.tools.length} 个工具 · ${service.permission==='ask'?'每次询问':'直接调用'}`;card.append(details);
    if(service.error){const error=document.createElement('p');error.className='mcp-error';error.textContent=service.error;card.append(error)}
    if(service.tools.length){const tools=document.createElement('p');tools.className='mcp-tools';tools.textContent=service.tools.join('、');card.append(tools)}
    const actions=document.createElement('div');actions.className='tool-row';
    const toggle=document.createElement('button');toggle.type='button';toggle.textContent=service.enabled?'停用':'启用';toggle.onclick=()=>saveMCP(current=>({...current,services:current.services.map(item=>item.id===service.id?{...item,enabled:!item.enabled}:item)}));
    actions.append(toggle);
    if(!service.builtin){const edit=document.createElement('button');edit.type='button';edit.textContent='编辑';edit.onclick=()=>openMCPForm(service);
      const remove=document.createElement('button');remove.type='button';remove.textContent='移除';remove.onclick=()=>{if(confirm(`移除 MCP 服务 ${service.id}？`))saveMCP(current=>({...current,services:current.services.filter(item=>item.id!==service.id)}))};actions.append(edit,remove)}
    card.append(actions);list.append(card);
  }
  if(mcpView.services.some(service=>service.status==='connecting'))setTimeout(()=>{if(document.body.dataset.currentPage==='mcp')loadMCP()},700);
}
function openMCPForm(service=null){
  mcpEditing=service?.id||null;$('mcp-form').hidden=false;$('mcp-form-heading').textContent=service?'编辑本地服务':'添加本地服务';
  $('mcp-id').value=service?.id||'';$('mcp-id').readOnly=Boolean(service);
  $('mcp-command').value=service?.command||'';$('mcp-args').value=(service?.args||[]).join('\n');$('mcp-cwd').value=service?.cwd||'';
  $('mcp-permission').value=service?.permission||'ask';$('mcp-id').focus();
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
setInterval(()=>{if(document.body.dataset.currentPage==='mcp'&&!mcpLoading&&$('mcp-form').hidden)loadMCP()},3000);

// Ticket 134: 模型 settings card and the header switch. The server keeps keys; this page only sees "…1234".
let modelsView=null,modelsSignature='',editingEndpoint=null,editingModel=null,modelBilling='call',modelsSaving=false;
function modelName(m){return (m.label||m.model)+(m.builtin?'':' · '+m.endpoint_name)}
function modelPrice(m){
  if(m.builtin)return '内置，价格在 character.toml';
  return m.billing==='call'?'每次 ¥'+m.cny_per_call:'输入 ¥'+m.input_cny_per_million+' / 输出 ¥'+m.output_cny_per_million+'（每百万 tokens）';
}
function fillModelSelect(select,view,value){
  select.replaceChildren(...view.models.map(m=>{const o=document.createElement('option');o.value=m.id;o.textContent=modelName(m)+(m.key_set?'':'（没有 key）');return o}));
  select.value=value;
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
  fillModelSelect($('use-peter'),view,view.use.peter);fillModelSelect($('use-background'),view,view.use.background);
  $('background-hint').textContent=view.background_restart?'记忆后台已改，完全退出 Peter 再打开才生效。':'Ombre 给记忆打标签、整理短期便签用这个。换了以后，完全退出 Peter 再打开才生效。';
  if(document.activeElement!==$('limit-daily'))$('limit-daily').value=view.limits.daily_cny;
  if(document.activeElement!==$('limit-monthly'))$('limit-monthly').value=view.limits.monthly_cny;
  const endpoints=$('endpoint-list');endpoints.replaceChildren();
  if(!view.endpoints.length){const p=document.createElement('p');p.className='muted';p.textContent='还没有添加接口。';endpoints.append(p)}
  for(const e of view.endpoints)endpoints.append(modelItem(e.name,e.base_url+' · '+(e.key_set?'key 已保存 '+e.key_hint:'没有 key'),()=>openEndpointForm(e),
    ()=>saveModels({endpoints:modelsPayload().endpoints.filter(x=>x.id!==e.id),models:modelsPayload().models.filter(m=>m.endpoint!==e.id)},'接口已删除。')));
  const models=$('model-list');models.replaceChildren();
  for(const m of view.models)models.append(modelItem(modelName(m),m.model+' · '+modelPrice(m)+(m.builtin?'':' · 思考'+(m.thinking?'开':'关')),
    m.builtin?null:()=>openModelForm(m),m.builtin?null:()=>saveModels({models:modelsPayload().models.filter(x=>x.id!==m.id)},'模型已删除。')));
  $('add-model').disabled=!view.endpoints.length;
}
function modelsPayload(){
  const v=modelsView;
  return {endpoints:v.endpoints.map(e=>({id:e.id,name:e.name,base_url:e.base_url})),
    models:v.models.filter(m=>!m.builtin).map(m=>{const out={};for(const k of ['id','endpoint','model','label','thinking','billing','cny_per_call','input_cny_per_million','cached_input_cny_per_million','output_cny_per_million','max_input_tokens'])if(m[k]!==undefined)out[k]=m[k];return out}),
    use:{...v.use},limits:{...v.limits}};
}
async function saveModels(change,done){
  if(!modelsView||modelsSaving)return false;
  modelsSaving=true;
  try{
    const r=await api('/api/models',{...modelsPayload(),...change});const data=await r.json();
    if(!r.ok){add('error',(data&&data.message)||'模型设置保存失败，请检查本机 Agent 连接后重试。');return false}
    modelsSignature='';renderModels(data.models);if(done)add('notice',done);return true;
  }catch(e){add('error','模型设置保存失败，请检查本机 Agent 连接后重试。');return false}
  finally{modelsSaving=false;$('model-switch').disabled=stopping||stopped}
}
async function useModel(role,id){
  if(!modelsView||modelsSaving||id===modelsView.use[role])return;
  modelsSaving=true;
  try{
    const r=await api('/api/models/use',{role,model:id});const data=await r.json();
    if(!r.ok){add('error',(data&&data.message)||'切换模型失败，请重试。');modelsSignature='';renderModels(modelsView);return}
    modelsSignature='';renderModels(data.models);
    const m=data.models.models.find(x=>x.id===id);
    add('notice',role==='peter'?'下一句开始，Peter 用 '+modelName(m)+'。':'记忆后台改成 '+modelName(m)+'，完全退出 Peter 再打开后生效。');
  }catch(e){add('error','切换模型失败，请检查本机 Agent 连接后重试。')}
  finally{modelsSaving=false;$('model-switch').disabled=stopping||stopped}
}
$('model-switch').onchange=e=>useModel('peter',e.target.value);
$('use-peter').onchange=e=>useModel('peter',e.target.value);
$('use-background').onchange=e=>useModel('background',e.target.value);
$('save-limits').onclick=()=>saveModels({limits:{daily_cny:$('limit-daily').value,monthly_cny:$('limit-monthly').value}},'费用上限已保存。');
function openEndpointForm(e){
  editingEndpoint=e||null;$('endpoint-form').hidden=false;$('add-endpoint').hidden=true;
  $('endpoint-name').value=e?e.name:'';$('endpoint-url').value=e?e.base_url:'';$('endpoint-key').value='';
  $('endpoint-key-hint').hidden=!e;$('endpoint-name').focus();
}
function closeEndpointForm(){$('endpoint-form').hidden=true;$('add-endpoint').hidden=false;$('endpoint-key').value='';editingEndpoint=null}
$('add-endpoint').onclick=()=>openEndpointForm(null);
$('endpoint-cancel').onclick=closeEndpointForm;
$('endpoint-form').onsubmit=async ev=>{
  ev.preventDefault();
  const item={name:$('endpoint-name').value,base_url:$('endpoint-url').value};
  if($('endpoint-key').value.trim())item.key=$('endpoint-key').value.trim();
  const endpoints=modelsPayload().endpoints;
  if(editingEndpoint){const i=endpoints.findIndex(x=>x.id===editingEndpoint.id);endpoints[i]={...endpoints[i],...item}}else endpoints.push(item);
  if(await saveModels({endpoints},editingEndpoint?'接口已更新。':'接口已添加，接着在下面添加这个接口的模型。'))closeEndpointForm();
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
  select.value=m?m.endpoint:modelsView.endpoints[0].id;
  $('model-name').value=m?m.model:'';$('model-label').value=m?m.label:'';
  $('model-per-call').value=m&&m.cny_per_call||'';$('model-input-price').value=m&&m.input_cny_per_million||'';
  $('model-cached-price').value=m&&m.billing==='tokens'&&m.cached_input_cny_per_million!==m.input_cny_per_million?m.cached_input_cny_per_million:'';
  $('model-output-price').value=m&&m.output_cny_per_million||'';$('model-max-input').value=m?m.max_input_tokens:32768;
  $('model-thinking').checked=m?m.thinking:true;setBilling(m?m.billing:'call');$('model-name').focus();
}
function closeModelForm(){$('model-form').hidden=true;$('add-model').hidden=false;editingModel=null}
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
  if(await saveModels({models},editingModel?'模型已更新。':'模型已添加。在上面“Peter 本人”或顶上的下拉里就能切过去。'))closeModelForm();
};
