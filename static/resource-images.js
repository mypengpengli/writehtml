/* Shared reference gallery for characters, setting cards and Word imports. */
let resourceImageItems = [], resourceImageTargets = [], resourceImageTarget = null;
let resourceImageDocument = null, resourceImageSelected = null, resourceImageDirty = false;
let resourceImageWorkId = null, resourceImageRequest = 0;
const resourceImageUrls = new Map();
const resourceImageFetches = new Map();
let resourceImageCacheVersion = 0;
const resourceImageVariants = {base:'基础',outfit:'服装',form:'形态',state:'状态',damaged:'损毁',season:'季节',logo:'标识',other:'其他'};
function clearResourceImageCache(){++resourceImageCacheVersion;resourceImageUrls.forEach(url=>URL.revokeObjectURL(url));resourceImageUrls.clear();resourceImageFetches.clear();}
function resetResourceImageGallery(){++resourceImageRequest;resourceImageItems=[];resourceImageTargets=[];resourceImageSelected=null;resourceImageDirty=false;$('resourceImageOverlay')?.classList.add('hidden');if($('resourceImageGrid'))$('resourceImageGrid').replaceChildren();if($('resourceImageEditor'))$('resourceImageEditor').replaceChildren();}

function ensureResourceImageGallery() {
  if ($('resourceImageOverlay')) return;
  const overlay = document.createElement('div');
  overlay.id = 'resourceImageOverlay'; overlay.className = 'overlay hidden';
  overlay.innerHTML = `<div class="overlay-card resource-image-library" role="dialog" aria-modal="true" aria-label="图片素材库">
    <div class="overlay-head"><b id="resourceImageTitle">图片素材库</b><span class="overlay-head-actions">
      <button class="ic" onclick="chooseResourceImageUpload()" title="上传图片" aria-label="上传图片">${svg('upload')}</button>
      <button class="ic" onclick="downloadCreativePackage()" title="导出创作素材包" aria-label="导出创作素材包">${svg('download')}</button>
      <button class="ic" onclick="closeResourceImageGallery()" title="关闭图片素材库" aria-label="关闭图片素材库">${svg('x')}</button>
    </span></div>
    <div class="resource-image-toolbar"><input id="resourceImageSearch" placeholder="搜索名称或说明…" oninput="renderResourceImageGallery()"><select id="resourceImageFilter" aria-label="图片分类" onchange="renderResourceImageGallery()"><option value="all">全部图片</option><option value="unbound">待归类</option><option value="character">人物</option><option value="location">地点</option><option value="item">道具</option><option value="organization">组织</option><option value="skill">技能</option><option value="rule">规则</option></select><span id="resourceImageCount"></span></div>
    <div class="resource-image-body"><div id="resourceImageGrid" class="resource-image-grid"></div><aside id="resourceImageEditor" class="resource-image-editor hidden"></aside></div>
    <input id="resourceImageUpload" class="hidden" type="file" accept="image/png,image/jpeg,image/webp,image/gif,image/avif" multiple onchange="uploadResourceImages(this.files)">
    <div id="resourceImageMessage" class="resource-image-message" role="status"></div>
  </div>`;
  document.body.append(overlay);
}

async function resourceImageUrl(imageId) {
  if (resourceImageUrls.has(imageId)) return resourceImageUrls.get(imageId);
  if (resourceImageFetches.has(imageId)) return resourceImageFetches.get(imageId);
  const version=resourceImageCacheVersion, requestToken=token;
  const promise=(async()=>{
    const response = await fetch(`/api/resource-images/${imageId}/content`, {headers: requestToken ? {Authorization:'Bearer '+requestToken} : {}});
    if (!response.ok) throw new Error(await responseError(response));
    const blob=await response.blob();
    if(version!==resourceImageCacheVersion||requestToken!==token)throw new Error('图片请求已过期');
    const url = URL.createObjectURL(blob); resourceImageUrls.set(imageId,url); return url;
  })();
  resourceImageFetches.set(imageId,promise);
  try{return await promise;}finally{if(resourceImageFetches.get(imageId)===promise)resourceImageFetches.delete(imageId);}
}

async function hydrateResourceImages(host) {
  await Promise.all([...host.querySelectorAll('[data-resource-image]')].map(async image => {
    const id = +image.dataset.resourceImage;
    try { const url = await resourceImageUrl(id); if (image.isConnected) image.src = url; }
    catch (_) { image.alt = '图片无法读取'; }
  }));
}

function resourceImageTile(image, inspect = false) {
  const target = image.target_name || '待归类';
  return `<button class="resource-image-tile ${image.selected?'selected':''} ${resourceImageSelected?.id===image.id?'current':''}" data-resource-image-id="${image.id}" onclick="${inspect ? `openResourceImageGalleryFromInspector(${image.id})` : `selectResourceImage(${image.id})`}" title="${esc(`${target} · ${image.label}`)}">
    <span class="resource-image-thumb"><img data-resource-image="${image.id}" alt="${esc(image.label)}">${image.selected?'<small>主图</small>':''}</span>
    <b>${esc(image.label)}</b><small>${esc(target)} · ${esc(resourceImageVariants[image.variant_kind] || '其他')}</small>
  </button>`;
}

async function openResourceImageGallery(targetType = null, targetId = null, documentId = null, imageId = null) {
  if (!currentWorkId) { showToast('请先选择作品','err'); return; }
  if(resourceImageDirty&&!await askCard({title:'放弃未保存的图片信息？',okText:'放弃修改'}))return;
  ensureResourceImageGallery();
  resourceImageTarget = targetType ? {type:targetType,id:targetId} : null;
  resourceImageDocument = documentId;
  resourceImageWorkId = currentWorkId;
  resourceImageSelected = null; resourceImageDirty = false;
  $('resourceImageEditor').classList.add('hidden'); $('resourceImageOverlay').classList.remove('hidden');
  $('resourceImageSearch').value = ''; $('resourceImageFilter').value = 'all';
  const request = ++resourceImageRequest;
  try {
    const [characters,cards] = await Promise.all([api(`/api/works/${resourceImageWorkId}/entities`,{method:'GET'}),api(`/api/works/${resourceImageWorkId}/production/cards`,{method:'GET'})]);
    if (request !== resourceImageRequest) return;
    resourceImageTargets = [...characters.filter(item=>item.kind==='人物').map(item=>({type:'character',id:item.id,name:item.name,category:'人物'})),
      ...cards.filter(item=>item.status==='confirmed').map(item=>({type:'card',id:item.id,name:item.name,category:productionCategoryLabels[item.category]}))];
    $('resourceImageTitle').textContent = documentId ? 'Word 图片素材' : targetType ? `${resourceImageTargets.find(item=>item.type===targetType&&item.id===targetId)?.name||'对象'} · 图片与衍生图` : '全书图片素材';
    await loadResourceImageGallery();
    if (imageId) await selectResourceImage(imageId);
  } catch(e) { $('resourceImageMessage').textContent=e.message; }
}

async function loadResourceImageGallery() {
  const request = ++resourceImageRequest;
  const params = new URLSearchParams();
  if (resourceImageTarget) { params.set('target_type',resourceImageTarget.type); params.set('target_id',resourceImageTarget.id); }
  if (resourceImageDocument) params.set('document_id',resourceImageDocument);
  const data = await api(`/api/works/${resourceImageWorkId}/resource-images?${params}`,{method:'GET'});
  if (request !== resourceImageRequest) return;
  resourceImageItems=data.items || []; renderResourceImageGallery();
  $('resourceImageTitle').textContent = resourceImageDocument ? 'Word 图片素材' : resourceImageTarget ? `${resourceImageTargets.find(item=>item.type===resourceImageTarget.type&&item.id===resourceImageTarget.id)?.name||'对象'} · 图片与衍生图` : '全书图片素材';
}

function renderResourceImageGallery() {
  const query=$('resourceImageSearch').value.trim().toLowerCase(), filter=$('resourceImageFilter').value;
  const items=resourceImageItems.filter(item=>(filter==='all'||filter==='unbound'&&!item.target_type||item.category===filter)&&`${item.label} ${item.target_name} ${item.description}`.toLowerCase().includes(query));
  $('resourceImageCount').textContent=`${items.length} 张`;
  $('resourceImageGrid').innerHTML=items.map(item=>resourceImageTile(item)).join('')||'<div class="empty">暂无图片</div>';
  hydrateResourceImages($('resourceImageGrid'));
}

function resourceImageDraftChanged() { resourceImageDirty=true; $('resourceImageMessage').textContent='修改待保存'; }

async function selectResourceImage(imageId) {
  if (resourceImageDirty && !await askCard({title:'放弃未保存的图片信息？',msg:'图片原文件不会删除。',okText:'放弃修改'})) return;
  const item=resourceImageItems.find(image=>image.id===imageId); if(!item)return;
  resourceImageSelected=item; resourceImageDirty=false;
  renderResourceImageGallery();
  const options=resourceImageTargets.map(target=>`<option value="${target.type}:${target.id}" ${item.target_type===target.type&&item.target_id===target.id?'selected':''}>${esc(target.category+' · '+target.name)}</option>`).join('');
  const parentOptions=resourceImageItems.filter(image=>image.id!==item.id&&image.target_type===item.target_type&&image.target_id===item.target_id).map(image=>`<option value="${image.id}" ${image.id===item.parent_image_id?'selected':''}>${esc(image.label)} #${image.id}</option>`).join('');
  const editor=$('resourceImageEditor'); editor.classList.remove('hidden');
  editor.innerHTML=`<div class="resource-image-editor-head"><b>图片 #${item.id}</b><button class="ic" title="收起图片详情" aria-label="收起图片详情" onclick="closeResourceImageEditor()">${svg('x')}</button></div>
    <button class="resource-image-large" onclick="viewResourceImage(${item.id})" title="查看原图"><img data-resource-image="${item.id}" alt="${esc(item.label)}"></button>
    <div class="resource-image-form" oninput="resourceImageDraftChanged()"><label>归属<select id="resourceImageOwner" ${item.target_type?'disabled':''} onchange="updateResourceImageParentOptions()"><option value="">待归类</option>${options}</select></label>
    <label>名称<input id="resourceImageLabel" maxlength="200" value="${esc(item.label)}"></label>
    <label>衍生类型<select id="resourceImageVariant">${Object.entries(resourceImageVariants).map(([kind,label])=>`<option value="${kind}" ${item.variant_kind===kind?'selected':''}>${label}</option>`).join('')}</select></label>
    <label>衍生自<select id="resourceImageParent"><option value="">未指定原图</option>${parentOptions}</select></label>
    <label>关联章节<select id="resourceImageChapter"><option value="">全书参考，未指定时点</option>${chapters.map(chapter=>`<option value="${chapter.id}" ${item.chapter_id===chapter.id?'selected':''}>第${chapter.ord}章 ${esc(chapter.title)}</option>`).join('')}</select></label>
    <label>说明<textarea id="resourceImageDescription" rows="3" maxlength="4000">${esc(item.description)}</textarea></label></div>
    ${item.source_name?`<details class="resource-image-source"><summary>来源 · ${esc(item.source_name)}</summary><pre>${esc(item.source_context)}</pre></details>`:''}
    <div class="resource-image-editor-actions"><button onclick="saveResourceImageMetadata()">保存绑定</button>${item.target_type&&!item.selected?'<button onclick="setResourceImagePrimary()">设为主图</button>':''}<button onclick="duplicateResourceImage()">复制</button><button class="danger-lite" onclick="removeResourceImage()">删除</button></div>`;
  $('resourceImageMessage').textContent=item.target_type?'已保存':'待归类';
  hydrateResourceImages(editor);
}

async function updateResourceImageParentOptions() {
  const [type,id]=$('resourceImageOwner').value.split(':');
  if(!id){$('resourceImageParent').innerHTML='<option value="">未指定原图</option>';return;}
  try{
    const data=await api(`/api/works/${resourceImageWorkId}/resource-images?target_type=${type}&target_id=${id}`,{method:'GET'});
    if($('resourceImageOwner')?.value!==`${type}:${id}`)return;
    $('resourceImageParent').innerHTML='<option value="">未指定原图</option>'+(data.items||[]).filter(image=>image.id!==resourceImageSelected.id).map(image=>`<option value="${image.id}">${esc(image.label)} #${image.id}</option>`).join('');
  }catch(e){showToast(e.message,'err');}
}

async function saveResourceImageMetadata() {
  if(!resourceImageSelected)return;
  const [type,id]=$('resourceImageOwner').value.split(':');
  if(!id){showToast('请选择归属对象；也可以保持待归类','err');return;}
  try{
    const saved=await api(`/api/resource-images/${resourceImageSelected.id}`,{method:'PUT',body:{target_type:type,target_id:+id,label:$('resourceImageLabel').value.trim(),description:$('resourceImageDescription').value,variant_kind:$('resourceImageVariant').value,parent_image_id:+$('resourceImageParent').value||null,chapter_id:+$('resourceImageChapter').value||null}});
    resourceImageDirty=false; await loadResourceImageGallery(); await selectResourceImage(saved.id);
    $('resourceImageMessage').textContent='已保存'; await refreshResourceImageSurfaces();
  }catch(e){$('resourceImageMessage').textContent='保存失败：'+e.message;}
}

async function setResourceImagePrimary() {
  if(resourceImageDirty){showToast('请先保存图片信息','err');return;}
  try{await api(`/api/resource-images/${resourceImageSelected.id}/select`,{body:{}});const id=resourceImageSelected.id;await loadResourceImageGallery();await selectResourceImage(id);await refreshResourceImageSurfaces();}
  catch(e){showToast(e.message,'err');}
}
async function duplicateResourceImage() {
  if(resourceImageDirty){showToast('请先保存图片信息','err');return;}
  try{const copy=await api(`/api/resource-images/${resourceImageSelected.id}/copy`,{body:{}});resourceImageTarget=null;resourceImageDocument=null;resourceImageDirty=false;await loadResourceImageGallery();await selectResourceImage(copy.id);}
  catch(e){showToast(e.message,'err');}
}
async function removeResourceImage() {
  const image=resourceImageSelected;
  if(!image||!await askCard({title:'删除这张图片？',msg:'人物或设定不会删除；衍生子图保留并解除此原图引用。',danger:true,okText:'删除'}))return;
  try{await api(`/api/resource-images/${image.id}`,{method:'DELETE'});resourceImageDirty=false;resourceImageSelected=null;$('resourceImageEditor').classList.add('hidden');if(resourceImageUrls.has(image.id))URL.revokeObjectURL(resourceImageUrls.get(image.id));resourceImageUrls.delete(image.id);await loadResourceImageGallery();await refreshResourceImageSurfaces();}
  catch(e){showToast(e.message,'err');}
}
async function closeResourceImageEditor(){if(resourceImageDirty&&!await askCard({title:'放弃未保存的图片信息？',okText:'放弃修改'}))return;resourceImageDirty=false;resourceImageSelected=null;$('resourceImageEditor').classList.add('hidden');}
async function closeResourceImageGallery(){if(resourceImageDirty&&!await askCard({title:'放弃未保存的图片信息？',okText:'放弃修改'}))return;resourceImageDirty=false;++resourceImageRequest;$('resourceImageOverlay').classList.add('hidden');}
async function viewResourceImage(id){try{window.open(await resourceImageUrl(id),'_blank','noopener');}catch(e){showToast(e.message,'err');}}
function chooseResourceImageUpload(){if(resourceImageDirty){showToast('请先保存图片信息','err');return;}$('resourceImageUpload').click();}
async function uploadResourceImages(files){
  const inputs=[...files];$('resourceImageUpload').value='';
  const params=new URLSearchParams();if(resourceImageTarget){params.set('target_type',resourceImageTarget.type);params.set('target_id',resourceImageTarget.id);}
  try{for(const file of inputs){const response=await fetch(`/api/works/${resourceImageWorkId}/resource-images?${params}`,{method:'POST',headers:{Authorization:'Bearer '+token,'X-File-Name':encodeURIComponent(file.name)},body:file});if(!response.ok)throw new Error(await responseError(response));}await loadResourceImageGallery();await refreshResourceImageSurfaces();$('resourceImageMessage').textContent=`已上传 ${inputs.length} 张图片`;}catch(e){$('resourceImageMessage').textContent='上传失败：'+e.message;await loadResourceImageGallery();}
}

function addResourceImageInspectorTab(){
  if(!['character','card'].includes(productionSelected?.type)||!productionSelected.item?.id)return;
  const body=$('productionInspectorBody');
  if(!body.querySelector('[data-inspector-panel="images"]')){const panel=document.createElement('div');panel.dataset.inspectorPanel='images';panel.className='hidden';body.querySelector('.production-inspector-form').append(panel);}
  if(!$('productionInspectorTabs').querySelector('[data-inspector-tab="images"]'))$('productionInspectorTabs').insertAdjacentHTML('beforeend','<button role="tab" data-inspector-tab="images" onclick="selectInspectorTab(\'images\')">图片</button>');
  $('productionInspectorTabs').classList.remove('hidden');
}
async function renderResourceImageInspector(){
  const selected=productionSelected, panel=$('productionInspectorBody').querySelector('[data-inspector-panel="images"]');
  if(!selected||!panel)return;
  panel.innerHTML='<span class="muted">读取图片…</span>';
  try{const data=await api(`/api/works/${currentWorkId}/resource-images?target_type=${selected.type}&target_id=${selected.item.id}`,{method:'GET'});if(productionSelected!==selected||!panel.isConnected)return;
    panel.innerHTML=`<div class="resource-image-inspector-actions"><button onclick="openResourceImageGalleryFromInspector()">${svg('image')}管理图片</button><button onclick="openResourceImageGallery()">待归类与全书图库</button></div><div class="resource-image-grid compact">${(data.items||[]).map(image=>resourceImageTile(image,true)).join('')||'<div class="empty">暂无图片</div>'}</div>`;hydrateResourceImages(panel);
  }catch(e){if(panel.isConnected)panel.textContent=e.message;}
}
function openResourceImageGalleryFromInspector(imageId=null){if(productionSelected)openResourceImageGallery(productionSelected.type,productionSelected.item.id,null,imageId);}
async function refreshResourceImageSurfaces(){
  clearEntityImageCache();await loadWikiEntities();
  if(inspectorTab==='images'&&productionSelected)await renderResourceImageInspector();
  if(!$('characterStateOverlay').classList.contains('hidden'))await loadCharacterState();
}
async function downloadCreativePackage(){
  if(!currentWorkId)return;
  if(resourceImageDirty){showToast('请先保存图片信息，再导出素材包','err');return;}
  try{if(!await saveNow())return;await flushProductionLayoutSave();await productionSaveQueue.wait();
    if(productionSelected?.type==='card'&&$('productionCardName')?.value.trim()&&!await saveProductionCardFromInspector(true)){showToast('设定尚未同步，已停止导出','err');return;}
    if(productionSelected?.type==='story_plan'&&!await saveStoryPlanFromInspector(true))return;
    const response=await fetch(`/api/works/${currentWorkId}/creative-package`,{headers:{Authorization:'Bearer '+token}});if(!response.ok)throw new Error(await responseError(response));
    const url=URL.createObjectURL(await response.blob()), anchor=document.createElement('a');anchor.href=url;anchor.download=`writehtml-work-${currentWorkId}.zip`;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);showToast('创作素材包已导出','ok');
  }catch(e){showToast('导出失败：'+e.message,'err');}
}
