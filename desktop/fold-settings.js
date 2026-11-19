
// 工单 392：设置页每张卡片收成可展开的分组，默认只开第一组；标题已有的情况下不再重复显示。
function foldSettingsSections(){
  const view=document.getElementById('more-view');
  if(!view||view.dataset.folded==='1')return;
  const cards=[...view.querySelectorAll('article.surface')];
  cards.forEach((card,index)=>{
    const heading=card.querySelector('h3');
    const title=heading?heading.textContent.trim():(card.querySelector('h4')?.textContent.trim()||'');
    if(!title)return;
    const details=document.createElement('details');
    details.className='settings-section';
    details.open=index===0;
    const summary=document.createElement('summary');
    summary.textContent=title;
    const body=document.createElement('div');
    body.className='section-body';
    card.parentNode.insertBefore(details,card);
    body.appendChild(card);
    details.append(summary,body);
    // 卡片里原来的标题已经被 summary 顶上去了，隐藏它，避免同一行字出现两次
    if(heading)heading.style.display='none';
  });
  view.dataset.folded='1';
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',foldSettingsSections);
else foldSettingsSections();
