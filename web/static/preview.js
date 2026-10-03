const $=id=>document.getElementById(id);
let epoch=0;
export function closePreview(){epoch++;$('preview-panel').hidden=true;$('preview-content').replaceChildren();}
export function showImage(url,title){epoch++;$('preview-panel').hidden=false;$('preview-title').textContent=title||'原图';$('download').hidden=true;const img=document.createElement('img');img.src=url;img.alt=title||'原图';$('preview-content').replaceChildren(img);}
export async function showArtifact(){ $('preview-panel').hidden=false;$('preview-content').textContent='文件预览将在文件中心接入后启用。'; }
