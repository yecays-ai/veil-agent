// Desktop IPC sends authenticated requests through the existing local Agent.
const $=id=>document.getElementById(id), col=$('col'), log=$('log'), box=$('box'), sendBtn=$('send');
const imageFile=$('image-file'),imageButton=$('image-button'),imagePreview=$('image-preview'),composer=$('composer');
const voiceHold=$('voice-hold');
const speechButton=$('speech-toggle'), speechStatus=$('speech-status');
const speechDraftKey='veil-speech-draft';
let speechRecorder=null,speechStream=null,speechChunks=[],speechSession=null,speechState='idle',speechPermissionPending=false,speechCancelled=false,speechDraftActive=false,speechTimer=null;
let after=0, busy=true, transcribingVoice=false, stopping=false, stopped=false, typing=null, sending=false;
let revealQueue=[], revealTimer=null, roundStartedAt=0, roundHasReply=false, previousMessage=null, voiceContextMenu=null;
let pendingApproval=null, activityEnabled=false, exiting=false;
let selectedImage=null;const awaitingImages=new Map();
let voiceRecorder=null,voiceStream=null,voiceChunks=[],voiceStarted=0,voiceTimer=null,voiceCancelled=false,voiceSending=false,voicePermissionPending=false;
const unsentVoiceBlobs=new Map();
let imageChoice=0;
const draftRequests=new Map();
const IMAGE_TYPES=new Set(['image/png','image/jpeg','image/gif','image/webp']);
const GROUP_GAP=15*60*1000;
function speechIsActive(){return speechState==='listening'||speechState==='processing'||!!speechRecorder||speechPermissionPending}
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
  if(e.kind==='voice_status'&&e.media?.id){
    const node=[...col.querySelectorAll('.msg')].find(item=>item.dataset.mediaId===e.media.id);
    if(node)updateVoiceMessage(node,e.media);
    return;
  }
  if(e.kind==='reset'){
    col.replaceChildren();typing=null;previousMessage=null;roundStartedAt=0;roundHasReply=false;
    scrollIfFollowing(()=>add('notice',e.text,e.id,e.time));
    return;
  }
  if(e.kind==='user'&&e.media?.kind==='voice'){
    const pending=[...col.querySelectorAll('.msg')].find(node=>node.dataset.mediaId===e.media.id);
    if(pending)pending.remove();
  }
  scrollIfFollowing(()=>add(e.kind,e.text,e.id,e.time,e.media));
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
function closeVoiceContextMenu(){
  if(!voiceContextMenu)return;
  voiceContextMenu.remove();voiceContextMenu=null;
  document.removeEventListener('pointerdown',dismissVoiceContextMenu,true);
  document.removeEventListener('keydown',dismissVoiceContextMenu);
}
function dismissVoiceContextMenu(event){
  if(event.type==='keydown'&&event.key!=='Escape')return;
  if(event.type==='pointerdown'&&voiceContextMenu?.contains(event.target))return;
  closeVoiceContextMenu();
}
function showVoiceContextMenu(event,wrap){
  event.preventDefault();closeVoiceContextMenu();
  const textBox=wrap.querySelector('.voice-transcript');
  if(!textBox)return;
  const menu=document.createElement('div');menu.className='voice-context-menu';menu.setAttribute('role','menu');
  for(const [label,available] of [['删除',false],['撤回',false],[textBox.hidden?'转文字':'收起文字',!!textBox.textContent.trim()]]){
    const item=document.createElement('button');item.type='button';item.setAttribute('role','menuitem');item.textContent=label;item.disabled=!available;
    if(!available&&label!=='转文字'&&label!=='收起文字')item.title='暂未开放';
    if(available)item.onclick=()=>{textBox.hidden=!textBox.hidden;closeVoiceContextMenu()};
    menu.appendChild(item);
  }
  document.body.appendChild(menu);voiceContextMenu=menu;
  menu.style.left=Math.max(8,Math.min(event.clientX,window.innerWidth-menu.offsetWidth-8))+'px';
  menu.style.top=Math.max(8,Math.min(event.clientY,window.innerHeight-menu.offsetHeight-8))+'px';
  document.addEventListener('pointerdown',dismissVoiceContextMenu,true);
  document.addEventListener('keydown',dismissVoiceContextMenu);
  menu.querySelector('button:not(:disabled)')?.focus();
}
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
  const play=document.createElement('button');play.type='button';play.className='voice-play';play.textContent='▶';play.setAttribute('aria-label','播放语音');
  const wave=document.createElement('span');wave.className='voice-wave';wave.setAttribute('aria-hidden','true');
  for(let i=0;i<9;i++){const bar=document.createElement('i');wave.appendChild(bar)}
  const clock=document.createElement('span');clock.className='voice-clock';clock.textContent=formatVoiceTime(media.duration||0);
  const status=document.createElement('span');status.className='voice-status';
  const audio=document.createElement('audio');audio.preload='metadata';audio.hidden=true;
  audio.setAttribute('aria-label',kind==='assistant'?'Peter 的语音消息':'语音消息');
  if(media.localUrl)audio.src=media.localUrl;else window.veilDesktop.mediaUrl(media.filename).then(url=>{audio.src=url}).catch(()=>{});
  audio.onloadedmetadata=()=>{clock.textContent=formatVoiceTime(audio.duration)};
  audio.onended=()=>{play.textContent='▶';play.setAttribute('aria-label','播放语音');wrap.classList.remove('playing')};
  play.onclick=async()=>{if(audio.paused){try{await audio.play();play.textContent='Ⅱ';play.setAttribute('aria-label','暂停语音');wrap.classList.add('playing')}catch{status.textContent='无法播放这段语音'}}else{audio.pause();play.textContent='▶';play.setAttribute('aria-label','播放语音');wrap.classList.remove('playing')}};
  row.append(play,wave,clock);wrap.append(row,audio,status);
  wrap.addEventListener('contextmenu',event=>showVoiceContextMenu(event,wrap));
  if(kind==='assistant'){
    const line=document.createElement('div');line.className='voice-transcript';line.hidden=true;line.textContent=(text||'').trim();wrap.append(line);
  }else updateVoiceMessage({querySelector:()=>wrap},media);
  return wrap;
}
function add(kind,text,id,time,media){
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
    if(media&&media.kind==='voice'){
      d.dataset.mediaId=media.id;
      d.appendChild(createVoiceBubble(kind,text,media));
    }else if(media&&media.id&&media.mime?.startsWith('audio/')){
      d.appendChild(createVoiceBubble(kind,text,media));
    }else if(media&&media.id){
      const image=document.createElement('img');image.className='msg-image';window.veilDesktop.mediaUrl(media.filename).then(url=>{image.src=url});image.alt=media.name||'图片';image.loading='lazy';d.appendChild(image);
      if(text&&text!=='[图片]'){const caption=document.createElement('div');caption.innerHTML=render(text);d.appendChild(caption)}
    }else d.innerHTML=render(text);
    previousMessage={kind,time:stamp};
  }
  else if(kind==='approval'){
    d.className='approval';d.dataset.id=id;
    const p=document.createElement('p');p.textContent=text;d.appendChild(p);
    const status=document.createElement('div');status.className='status';status.setAttribute('aria-live','polite');d.appendChild(status);
    for(const [label,ans,cls] of [['同意','y','yes'],['拒绝','n','']]){
      const b=document.createElement('button');b.type='button';b.dataset.answer=ans;b.textContent=label;b.className=cls;d.appendChild(b);
    }
  } else {d.className=kind==='error'?'error':'notice';d.textContent=text}
  if(typing)col.insertBefore(d,typing);else col.appendChild(d);
}
function setBusy(b){
  busy=b;const waiting=b||revealQueue.length>0;sendBtn.disabled=waiting||sending||voiceSending||stopping||stopped||speechIsActive();
  voiceHold.disabled=waiting||sending||stopping||stopped||voiceSending||speechIsActive();
  if(waiting&&!typing&&!stopped){typing=document.createElement('div');typing.id='typing';typing.textContent=transcribingVoice?'正在转写语音…':($('title').textContent||'')+' 正在回复…';col.appendChild(typing)}
  else if(waiting&&typing)typing.textContent=transcribingVoice?'正在转写语音…':($('title').textContent||'')+' 正在回复…';
  if((!waiting||stopped)&&typing){typing.remove();typing=null}
}
async function api(path,body,timeoutMs=30000){
  const result=await window.veilDesktop.request(path,body,timeoutMs);
  return {ok:result.ok,status:result.status,json:async()=>result.data};
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
function renderActivity(record){
  $('activity-details').hidden=!record;
  if(!record){$('activity-record').textContent='';return}
  const lines=[record.started_at+' → '+record.finished_at,
    '结果：'+record.result+'；'+record.result_reason,
    '决定/结束信息：'+record.decision,
    '已留存文件：'+((record.lab_files_changed||[]).join('、')||'无')];
  for(const item of record.tools||[])lines.push(item.tool+' | '+item.path+' | '+item.state+
    (item.result_state==='结果过长'?'；结果过长，未提供给模型':''));
  if(record.next_step)lines.push('下一步：'+record.next_step);
  lines.push('本次费用：¥'+record.cost_cny);
  $('activity-record').textContent=lines.join('\n');
}
async function poll(){
  try{
    const r=await api('/api/events?after='+after);
    if(!r.ok)throw new Error(r.status);
    const s=await r.json();
    $('offline').style.display='none';
    stopping=s.stopping===true&&!s.stopped;stopped=s.stopped===true;
    $('ended').style.display=stopped?'block':'none';
    $('service-status').textContent='服务运行状态：'+(stopped?'已停止':stopping?'退出请求已收到，正在保存并关闭':'运行中');
    $('service-status-detail').textContent=$('service-status').textContent;
    activityEnabled=s.activity_enabled===true;
    const next=s.next_wake_at?new Date(s.next_wake_at).toLocaleString():'今日次数已用完或尚未排期';
    const last=s.last_result?'；上次：'+s.last_result:'';
    $('activity-status').textContent='自主活动状态：'+(!activityEnabled?'已暂停':(s.autonomy_busy?'正在活动':'已开启'))+'；下次唤醒：'+next+last;
    renderActivity(s.last_activity);
    $('activity-toggle').textContent=activityEnabled?'暂停自主活动':'开启自主活动';
    $('activity-toggle').disabled=stopping||stopped||exiting;
    $('exit-service').disabled=stopping||stopped||exiting;
    document.title=s.name;$('title').textContent=s.name;
    const capabilityText=s.capability_record||'No capability updates recorded yet.';
    if($('capability-record-text').textContent!==capabilityText)$('capability-record-text').textContent=capabilityText;
    const currentCapabilities=s.current_capabilities||'Current capabilities are not available.';
    if($('current-capabilities-text').textContent!==currentCapabilities)$('current-capabilities-text').textContent=currentCapabilities;
    const memoryPanel=$('memory-panel');
    memoryPanel.hidden=!s.memory_panel_url;
    memoryPanel.onclick=()=>window.veilDesktop.openMemory();
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
  }catch(e){$('offline').style.display='block';$('service-status').textContent='服务运行状态：连接中断';$('service-status-detail').textContent=$('service-status').textContent;$('activity-toggle').disabled=true;$('exit-service').disabled=true}
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
    $('activity-status').textContent='自主活动状态：'+(activityEnabled?'已开启':'已暂停');
    $('activity-toggle').textContent=activityEnabled?'暂停自主活动':'开启自主活动';
  }catch(e){add('error','自主活动状态保存失败，请检查连接后重试。')}
  finally{$('activity-toggle').disabled=stopping||stopped||exiting}
};
$('exit-service').onclick=async()=>{
  if(exiting||stopped||!confirm('确定退出 Peter 网页服务吗？'))return;
  exiting=true;$('exit-service').disabled=true;$('activity-toggle').disabled=true;
  $('service-status').textContent='服务运行状态：正在退出…';
  try{
    const r=await api('/api/exit',{});
    let result={};try{result=await r.json()}catch(e){}
    if(!r.ok||!result.ok)throw new Error(r.status);
    if(result.state==='stopped'){
      stopping=false;stopped=true;
      $('ended').style.display='block';$('service-status').textContent='服务运行状态：已停止';
      sendBtn.disabled=true;$('activity-toggle').disabled=true;$('exit-service').disabled=true;
    }else if(result.state==='accepted'||result.state==='stopping'){
      exiting=false;stopping=true;
      $('ended').style.display='none';
      $('service-status').textContent='服务运行状态：退出请求已收到，正在保存并关闭';
      sendBtn.disabled=true;$('activity-toggle').disabled=true;$('exit-service').disabled=true;
    }else throw new Error('invalid exit response');
  }catch(e){
    exiting=false;
    $('service-status').textContent='服务运行状态：退出结果未确认，正在核对';
    add('error','没有收到退出确认；页面会继续核对服务状态，请不要重复启动。');
  }
};
$('reconnect').onclick=async()=>{
  const button=$('reconnect');button.disabled=true;button.textContent='正在重新连接…';
  try{await window.veilDesktop.reconnect()}finally{button.disabled=false;button.textContent='重新连接'}
};
function theme(t){
  if(t)document.documentElement.setAttribute('data-theme',t);else document.documentElement.removeAttribute('data-theme');
  const dark=t==='dark';
  $('theme').textContent=dark?'浅色':'深色';
}
try{theme(localStorage.getItem('veil-theme'))}catch(e){theme(null)}
$('theme').onclick=()=>{
  const dark=$('theme').textContent==='浅色',t=dark?'light':'dark';
  theme(t);try{localStorage.setItem('veil-theme',t)}catch(e){}
};
const toolPanel=document.querySelector('#tool-panel');
col.addEventListener('click',event=>{
  const button=event.target.closest('.approval button[data-answer]');
  if(!button)return;
  const card=button.closest('.approval');
  approve(card,card.querySelector('.status'),button.dataset.answer,Number(card.dataset.id));
});
document.querySelector('#toolbar-toggle').onclick=()=>{toolPanel.hidden=!toolPanel.hidden};
document.querySelector('#collapse').onclick=()=>window.veilDesktop.collapse();
document.querySelector('#expand').onclick=()=>window.veilDesktop.expand();
window.veilDesktop.onShowActivity(()=>{toolPanel.hidden=false;const details=document.querySelector('#activity-details');if(!details.hidden)details.open=true});
document.querySelector('#capability-record').onclick=()=>{document.querySelector('#capability-details').open=!document.querySelector('#capability-details').open};
document.querySelector('#activity-status').title='自主活动状态';
setBusy(true);poll();box.focus();
