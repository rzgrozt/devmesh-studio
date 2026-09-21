from __future__ import annotations

TOOL_WIDGET_URI = "ui://devmesh/change-review-v3.html"
TOOL_WIDGET_MIME = "text/html;profile=mcp-app"

# This app is deliberately a review surface rather than a universal inspector.
# Only change/diff tools advertise it, and its one-line summary starts collapsed.
TOOL_WIDGET_HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{color-scheme:light dark;--card:#12141a;--body:#0d0f14;--hover:#191c24;--line:#2a2e38;--text:#f3f4f6;--muted:#969da9;--green:#74d68a;--green-bg:#163b24;--red:#f08282;--red-bg:#421e24;--amber:#e9b96e;--accent:#a79af7;--gutter:#777f8c}
*{box-sizing:border-box}html,body{margin:0;background:transparent;color:var(--text);font:13px/1.45 Inter,ui-sans-serif,system-ui,-apple-system,sans-serif}body{padding:4px}
.card{overflow:hidden;border:1px solid var(--line);border-radius:12px;background:var(--card)}
.head,.file-head{width:100%;border:0;color:inherit;font:inherit;text-align:left;cursor:pointer}.head{display:grid;grid-template-columns:32px minmax(0,1fr) auto 18px;align-items:center;gap:10px;min-height:52px;padding:7px 11px;background:transparent}.head:hover,.file-head:hover{background:var(--hover)}
.icon{display:grid;width:32px;height:32px;place-items:center;border-radius:9px;background:#8b7cf61c;color:var(--accent);font:700 17px/1 ui-monospace,monospace}.main{min-width:0}.title{font-weight:600}.sub{display:block;overflow:hidden;color:var(--muted);font:11px/1.35 ui-monospace,SFMono-Regular,Consolas,monospace;text-overflow:ellipsis;white-space:nowrap}
.stats{display:flex;gap:7px;font:12px/1 ui-monospace,SFMono-Regular,Consolas,monospace}.add{color:var(--green)}.del{color:var(--red)}.chev{color:var(--muted);transition:transform .15s}.open>.head .chev{transform:rotate(180deg)}
.body{display:none;border-top:1px solid var(--line);background:var(--body)}.open>.body{display:block}.notice{padding:12px;color:var(--muted)}.notice.error{color:var(--red)}
.file+.file{border-top:1px solid var(--line)}.file-head{display:grid;grid-template-columns:22px minmax(0,1fr) auto 16px;align-items:center;gap:9px;min-height:40px;padding:6px 11px;background:transparent}.kind{display:grid;width:20px;height:20px;place-items:center;border-radius:6px;background:#ffffff0b;color:var(--amber);font:700 10px/1 ui-monospace,monospace}.kind.A{color:var(--green);background:#4ade8015}.kind.D{color:var(--red);background:#fb718515}.path{overflow:hidden;font:12px/1.4 ui-monospace,SFMono-Regular,Consolas,monospace;text-overflow:ellipsis;white-space:nowrap}.file .chev{font-size:11px}.file.open .file-head .chev{transform:rotate(180deg)}
.diff{display:none;max-height:460px;overflow:auto;border-top:1px solid var(--line);background:#0a0c10;font:11px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace}.file.open .diff{display:block}.dline{display:grid;grid-template-columns:42px 42px minmax(max-content,1fr);min-height:18px}.dline>span{padding:0 7px}.num{border-right:1px solid #ffffff08;color:var(--gutter);text-align:right;user-select:none}.code{white-space:pre}.dline.added{background:color-mix(in srgb,var(--green-bg) 66%,transparent)}.dline.removed{background:color-mix(in srgb,var(--red-bg) 66%,transparent)}.dline.added .code{color:#b5e8bf}.dline.removed .code{color:#ffc0c0}.dline.hunk{background:#8b7cf612;color:#c7befd}.dline.hunk .num{color:transparent}.dline.meta{color:#7cb8d8}.dline.meta .num{color:transparent}
.empty{padding:14px;text-align:center;color:var(--muted)}
@media(prefers-color-scheme:light){:root{--card:#fff;--body:#fafbfc;--hover:#f3f4f6;--line:#e2e5ea;--text:#17191f;--muted:#6f7784;--green:#22863a;--green-bg:#dff5e3;--red:#cf3030;--red-bg:#ffe1e1;--amber:#9a6700;--accent:#6555c7;--gutter:#8b929d}.diff{background:#fff}.dline.added .code{color:#155d27}.dline.removed .code{color:#9e2020}}
@media(max-width:520px){.head{grid-template-columns:30px minmax(0,1fr) auto 16px;gap:8px}.icon{width:30px;height:30px}.dline{grid-template-columns:34px 34px minmax(max-content,1fr)}.dline>span{padding:0 5px}}
</style></head><body><main class="card" id="card">
<button class="head" id="toggle" type="button" aria-expanded="false"><span class="icon">±</span><span class="main"><span class="title" id="title">Changes ready</span><span class="sub" id="subtitle">Open to review</span></span><span class="stats"><span class="add" id="adds">+0</span><span class="del" id="dels">−0</span></span><span class="chev">⌄</span></button>
<section class="body" id="body"></section></main>
<script>
const $=id=>document.getElementById(id);const state={input:{},output:{},expanded:false};
function unwrap(value){return value?.structuredContent||value||{}}
function cleanPath(raw){return (raw||'').split('\t')[0].replace(/^[ab]\//,'')}
function extractDiff(result,input){if(typeof result?.diff==='string')return result.diff;if(typeof result?.patch==='string')return result.patch;if(typeof result?.stdout==='string'){const at=result.stdout.indexOf('diff --git ');if(at>=0)return result.stdout.slice(at);if(result.stdout.startsWith('--- '))return result.stdout}return typeof input?.patch==='string'?input.patch:''}
function splitFiles(patch){if(!patch)return[];const lines=patch.replace(/\r\n/g,'\n').split('\n'),files=[];let current=null,pendingOld='',pendingNew=false;
 const start=(path,kind='M')=>{current={path:path||'unknown file',kind,lines:[],adds:0,dels:0};files.push(current)};
 for(const line of lines){if(line.startsWith('diff --git ')){const m=line.match(/^diff --git a\/(.+) b\/(.+)$/);start(m?m[2]:'unknown file');current.lines.push(line);continue}
  if(line.startsWith('--- ')){pendingOld=cleanPath(line.slice(4));pendingNew=Boolean(current&&current.lines.some(value=>value.startsWith('@@')));if(current&&!pendingNew)current.lines.push(line);continue}
  if(line.startsWith('+++ ')){const next=cleanPath(line.slice(4)),kind=next==='/dev/null'?'D':pendingOld==='/dev/null'?'A':'M';if(!current||pendingNew){start(next==='/dev/null'?pendingOld:next,kind);current.lines.push('--- '+pendingOld)}else{current.path=next==='/dev/null'?pendingOld:next;current.kind=kind}current.lines.push(line);pendingNew=false;continue}
  if(!current)continue;current.lines.push(line);if(line.startsWith('+')&&!line.startsWith('+++'))current.adds++;if(line.startsWith('-')&&!line.startsWith('---'))current.dels++}
 return files.filter(f=>f.lines.length)
}
function lineNode(line,counters){let cls='',old='',now='';if(line.startsWith('@@')){cls='hunk';const m=line.match(/@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);if(m){counters.old=+m[1];counters.now=+m[2]}}else if(line.startsWith('+')&&!line.startsWith('+++')){cls='added';now=counters.now++}else if(line.startsWith('-')&&!line.startsWith('---')){cls='removed';old=counters.old++}else if(line.startsWith('diff ')||line.startsWith('index ')||line.startsWith('---')||line.startsWith('+++'))cls='meta';else{old=counters.old++;now=counters.now++}
 const row=document.createElement('div');row.className='dline '+cls;[old,now,line].forEach((value,index)=>{const span=document.createElement('span');span.className=index===2?'code':'num';span.textContent=value;row.append(span)});return row}
function fileNode(file,single){const root=document.createElement('section');root.className='file'+(single?' open':'');const head=document.createElement('button');head.type='button';head.className='file-head';head.setAttribute('aria-expanded',String(single));const kind=document.createElement('span');kind.className='kind '+file.kind;kind.textContent=file.kind;const path=document.createElement('span');path.className='path';path.textContent=file.path;path.title=file.path;const stats=document.createElement('span');stats.className='stats';stats.innerHTML='<span class="add">+'+file.adds+'</span><span class="del">−'+file.dels+'</span>';const chev=document.createElement('span');chev.className='chev';chev.textContent='⌄';head.append(kind,path,stats,chev);const diff=document.createElement('div');diff.className='diff';const counters={old:0,now:0};for(const line of file.lines)diff.append(lineNode(line,counters));head.onclick=()=>{root.classList.toggle('open');head.setAttribute('aria-expanded',String(root.classList.contains('open')));resize()};root.append(head,diff);return root}
function summary(result,failed){const box=document.createElement('div');box.className='notice'+(failed?' error':'');box.textContent=failed?(result?.error||'The change could not be completed.'):(result?.valid===false?(result?.stderr||'Patch validation failed.'):(result?.valid===true?'Patch is valid and ready to apply.':'No textual changes to review.'));return box}
function render(){const payload=unwrap(state.output),result=payload.result??payload,tool=payload.tool||state.input?.tool||'',failed=payload.status==='error'||payload.isError;const patch=extractDiff(result,state.input),files=splitFiles(patch),adds=files.reduce((n,f)=>n+f.adds,0),dels=files.reduce((n,f)=>n+f.dels,0);let title=failed?'Change failed':files.length?'Changes ready':'No changes';if(tool==='patch_preview'&&!failed)title=result?.valid===false?'Patch needs attention':'Patch ready';if(tool==='git_show'&&files.length)title='Revision changes';$('title').textContent=title;$('subtitle').textContent=files.length?(files.length===1?files[0].path:files.length+' files changed'):(result?.paths?.join(', ')||state.input?.path||'Nothing to review');$('adds').textContent='+'+adds;$('dels').textContent='−'+dels;$('body').replaceChildren();if(files.length)for(const file of files)$('body').append(fileNode(file,files.length===1));else $('body').append(summary(result,failed));resize()}
function resize(){requestAnimationFrame(()=>window.openai?.notifyIntrinsicHeight?.(document.documentElement.scrollHeight))}
$('toggle').onclick=()=>{state.expanded=!state.expanded;$('card').classList.toggle('open',state.expanded);$('toggle').setAttribute('aria-expanded',String(state.expanded));resize()};
window.addEventListener('message',event=>{if(event.source!==window.parent)return;const m=event.data;if(!m||m.jsonrpc!=='2.0')return;if(m.method==='ui/notifications/tool-input')state.input=m.params||{};if(m.method==='ui/notifications/tool-result')state.output=m.params?.structuredContent||m.params||{};render()},{passive:true});
window.addEventListener('openai:set_globals',event=>{const g=event.detail?.globals||event.detail||{};if(g.toolInput!==undefined)state.input=g.toolInput;if(g.toolOutput!==undefined)state.output=g.toolOutput;render()});
state.input=window.openai?.toolInput||{};state.output=window.openai?.toolOutput||{};new ResizeObserver(resize).observe(document.body);render();
</script></body></html>'''


def widget_resource() -> dict:
    return {
        "uri": TOOL_WIDGET_URI,
        "name": "DevMesh change review",
        "title": "DevMesh change review",
        "description": "Compact, collapsible, file-by-file review for code changes and diffs.",
        "mimeType": TOOL_WIDGET_MIME,
        "_meta": {"ui": {"prefersBorder": False}},
    }
