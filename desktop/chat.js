Warning: truncated output (original token count: 20784)
Total output lines: 1160

// Desktop IPC sends authenticated requests through the existing local Agent.
const $=id=>document.getElementById(id), col=$('col'), log=$('log'), box=$('box'), sendBtn=$('send');
const imageFile=$('image-file'),imageButton=$('image-button'),imagePreview=$('image-preview'),composer=$('composer');
const voiceHold=$('voice-hold');
const speechButton=$('speech-toggle'), speechStatus=$('speech-status');
const speechDraftKey='veil-speech-draft';
let speechRecorder=null,speechStream=null,speechChunks=[],speechSession=null,speechState='idle',speechPermissionPending=false,speechCancelled=false,speechDraftActive=false,speechTimer=null;
let after=0, busy=true, transcribingVoice=false, stopping=false, stopped=false, typing=null, sending=false;
let revealQueue=[], revealTimer=null, roundStartedAt=0, roundHasReply=false, previousMessage=null;
let pendingApproval=null, activityEnabled=false, exiting=false;
const recentErrors=new Map();
let memoryAvailable=false,memoryLoaded=false,memoryLoading=false,memoryCursor='',memoryItems=[];
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
  if(!['chat','activity','memory','more'].includes(name))return;
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
  speechState=state;speechStatus.dataset.state=state;speechStatus.textContent=message;
  const active=speechIsActive();
  speechButton.setAttribute('aria-pressed',String(active));
  speechButton.setAttribute('aria-label',active?'停止语音识别':'开始语音识别');
  speechButton.title=active?'停止语音识别':'开始语音识别';
  speechButton.innerHTML=window.veilIcon(state==='listening'?'square-x':'mic');
  box.disabled=active;
  sendBtn.disabled=busy||revealQueue.length>0||sending||stopping||stopped||active;
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
  if(kind==='notice'){notify(text);return}
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
  const quoted=(node.dataset.messageText||'').split('\n').map(line=>'> '+line).join('\n');
  const prefix=box.value.trim()?box.value.replace(/\s+$/,'')+'\n\n':'';
  box.value=prefix+`> ${who}：\n`+quoted;resizeBox();box.focus();box.setSelectionRange(box.value.length,box.value.length);
}
async function showMessageMemory(node){
  const layer=document.createElement('div');layer.className='chat-confirm-layer';
  const backdrop=document.createElemen…8784 tokens truncated…ype=>composer.addEventListener(type,e=>{e.preventDefault();if(type==='drop')chooseImage(e.dataTransfer?.files?.[0]);composer.classList.remove('image-drop-active')}));
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
function resetVoiceButton(){clearInterval(voiceTimer);voiceTimer=null;voiceHold.dataset.recording='false';voiceHold.textContent='按住说话'}
async function startVoice(){
  if(voiceRecorder||voicePermissionPending||voiceSending||busy||sending||stopping||stopped||speechIsActive())return;
  if(!navigator.mediaDevices?.getUserMedia||typeof MediaRecorder==='undefined'){add('error','当前环境不支持录音。');return}
  voiceCancelled=false;voicePermissionPending=true;voiceChunks=[];
  try{
    const stream=await navigator.mediaDevices.getUserMedia({audio:true});voicePermissionPending=false;
    if(voiceCancelled){stream.getTracks().forEach(track=>track.stop());voiceCancelled=false;resetVoiceButton();return}
    voiceStream=stream;
    const mime=voiceMime();voiceRecorder=new MediaRecorder(voiceStream,mime?{mimeType:mime}:undefined);voiceStarted=Date.now();
    voiceRecorder.ondataavailable=e=>{if(e.data?.size)voiceChunks.push(e.data)};
    voiceRecorder.onstop=finishVoice;
    voiceRecorder.start(500);voiceHold.dataset.recording='true';voiceHold.textContent='松开发送 · 0:00';
    voiceTimer=setInterval(()=>{const seconds=Math.floor((Date.now()-voiceStarted)/1000);voiceHold.textContent='松开发送 · '+Math.floor(seconds/60)+':'+String(seconds%60).padStart(2,'0');if(seconds>=120)stopVoice(false)},250);
  }catch(e){voicePermissionPending=false;stopVoiceTracks();voiceRecorder=null;resetVoiceButton();if(!voiceCancelled)add('error','无法使用麦克风，请在系统或浏览器设置中允许后重试。');voiceCancelled=false}
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
  if(voiceCancelled){add('notice','已取消这条录音。');return}
  const mime=(recorder.mimeType||voiceChunks[0]?.type||'audio/webm').split(';')[0];
  const blob=new Blob(voiceChunks,{type:mime});voiceChunks=[];
  if(!blob.size){add('error','没有录到声音，请重试。');return}
  if(blob.size>10*1024*1024){add('error','录音超过 10 MB，未保存；请缩短后重试。');return}
  voiceSending=true;setBusy(busy);voiceHold.textContent='正在发送…';
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
voiceHold.addEventListener('pointerdown',e=>{if(e.button!==undefined&&e.button!==0)return;e.preventDefault();startVoice()});
voiceHold.addEventListener('pointerup',e=>{e.preventDefault();stopVoice(false)});
voiceHold.addEventListener('pointerleave',()=>{if(voiceRecorder||voicePermissionPending)stopVoice(true)});
voiceHold.addEventListener('keydown',e=>{if(e.code==='Space'&&!e.repeat){e.preventDefault();startVoice()}});
window.addEventListener('keyup',e=>{if(e.code==='Space'&&(voiceRecorder||voicePermissionPending)){e.preventDefault();stopVoice(false)}});
window.addEventListener('blur',()=>{if(voiceRecorder||voicePermissionPending)stopVoice(true)});
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
    if((!text&&!selectedImage)||busy||revealQueue.length||stopping||stopped||sending||voiceSending||speechIsActive())return;
  const attachedImage=selectedImage;
  if(attachedImage&&text.startsWith('/')){add('error','图片不能和命令一起发送；图片仍保留在发送区。');return}
  if(!text)text='[图片]';
  const payloadKey=text+'\0'+(attachedImage?.key||'');
  if(fromBox&&!draftRequests.has(payloadKey)){
    draftRequests.set(payloadKey,crypto.randomUUID());
  }
  const requestId=fromBox?draftRequests.get(payloadKey):crypto.randomUUID();
  if(attachedImage)awaitingImages.set(requestId,attachedImage);
  if(attachedImage?.cancel)attachedImage.cancel.disabled=true;
  sending=true;setBusy(true);
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
        if(box.value.trim()===text){
          box.value='';resizeBox();
          try{sessionStorage.removeItem(speechDraftKey)}catch(e){}
          speechDraftActive=false;
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
$('glance-toggle').onclick=async()=>{
  $('glance-toggle').disabled=true;
  try{
    const enabled=$('glance-toggle').textContent==='开启瞄一眼';
    const r=await api('/api/glance',{enabled});
    if(!r.ok)throw new Error(r.status);
    poll();
  }catch(e){$('glance-status').textContent='设置保存失败：请检查本机 Agent 连接后重试。';add('error','瞄一眼设置保存失败，请检查连接后重试。')}
  finally{$('glance-toggle').disabled=stopping||stopped||exiting}
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
const appearanceDefaults={theme:'light',windowOpacity:88,pageOpacity:{chat:88,activity:88,memory:88,more:88},bubbleOpacity:90,backgroundOpacity:100,petSize:320};
function clampSetting(value,min,max,fallback){const n=Number(value);return Number.isFinite(n)?Math.max(min,Math.min(max,Math.round(n))):fallback}
function loadAppearance(){
  try{
    const saved=JSON.parse(localStorage.getItem(APPEARANCE_KEY)||'{}');
    const pageOpacity={};
    for(const page of ['chat','activity','memory','more'])pageOpacity[page]=clampSetting(saved.pageOpacity?.[page],20,100,clampSetting(saved.windowOpacity,20,100,88));
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
  for(const page of ['chat','activity','memory','more']){
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
function setAllWindowOpacity(value){appearance.windowOpacity=clampSetting(value,20,100,88);for(const page of ['chat','activity','memory','more'])appearance.pageOpacity[page]=appearance.windowOpacity;applyAppearance();storeAppearance()}
$('window-opacity').oninput=()=>setAllWindowOpacity($('window-opacity').value);
for(const page of ['chat','activity','memory','more'])$(`${page}-opacity`).oninput=()=>{appearance.pageOpacity[page]=clampSetting($(`${page}-opacity`).value,20,100,88);applyAppearance();storeAppearance()};
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
$('memory-search').addEventListener('input',renderMemoryList);
$('memory-reload').onclick=()=>{memoryItems=[];memoryCursor='';memoryLoaded=false;loadMemoryCatalog(true)};
document.querySelectorAll('[data-page]').forEach(button=>button.onclick=()=>{
  if(detachedViews.has(button.dataset.page)){window.veilDesktop.focusView(button.dataset.page);return}
  setPage(button.dataset.page);
  if(button.dataset.page==='memory'&&memoryAvailable&&!memoryLoaded&&!memoryLoading)loadMemoryCatalog(true);
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
if(detachedView&&['chat','activity','memory','more'].includes(detachedView)){
  document.body.classList.add('detached-window');$('detached-header').hidden=false;
  $('detached-title').textContent='Peter · '+({chat:'聊天',activity:'活动记录',memory:'记忆',more:'更多'}[detachedView]);setPage(detachedView);
}else{setPage('chat');box.focus()}
setBusy(true);poll();
