from __future__ import annotations

from urllib.parse import urlparse


LIVE_WIDGET_URI = "ui://devmesh/live-activity-v1.html"
LIVE_WIDGET_MIME = "text/html;profile=mcp-app"


LIVE_WIDGET_HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{color-scheme:light dark;--card:#12151b;--panel:#0d1015;--line:#2b303a;--text:#f1f3f5;--muted:#929aa7;--green:#67d391;--red:#f07c86;--amber:#edb85f;--blue:#7eb6ff;--violet:#a99cf5}
*{box-sizing:border-box}html,body{margin:0;background:transparent;color:var(--text);font:13px/1.42 Inter,ui-sans-serif,system-ui,-apple-system,sans-serif}body{padding:4px}.card{overflow:hidden;border:1px solid var(--line);border-radius:12px;background:var(--card)}
header{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:11px 13px;border-bottom:1px solid var(--line)}.brand{font-weight:650;letter-spacing:.01em}.identity{display:flex;min-width:0;align-items:center;gap:7px;color:var(--muted);font-size:12px}.identity span:last-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.dot{width:8px;height:8px;flex:0 0 auto;border-radius:50%;background:var(--muted)}.connected .dot{background:var(--green);box-shadow:0 0 0 3px #67d39119}.reconnecting .dot{background:var(--amber);animation:pulse 1.2s infinite}.expired .dot,.disconnected .dot{background:var(--red)}@keyframes pulse{50%{opacity:.35}}
.body{display:grid;grid-template-columns:minmax(0,1fr) minmax(180px,38%);height:330px}.timeline{min-width:0;overflow:auto;border-right:1px solid var(--line);background:var(--panel)}.empty{display:grid;height:100%;place-items:center;padding:24px;color:var(--muted);text-align:center}.row{border-bottom:1px solid #ffffff0b}.row summary{display:grid;grid-template-columns:64px minmax(0,1fr) 74px;align-items:center;gap:8px;min-height:36px;padding:6px 10px;cursor:pointer;list-style:none}.row summary::-webkit-details-marker{display:none}.row.newest summary{background:#a99cf50c}.time{color:var(--muted);font:11px/1.2 ui-monospace,SFMono-Regular,Consolas,monospace}.tool{overflow:hidden;font:12px/1.3 ui-monospace,SFMono-Regular,Consolas,monospace;text-overflow:ellipsis;white-space:nowrap}.state{justify-self:end;font-size:11px}.state.running{color:var(--blue)}.state.success{color:var(--green)}.state.failed{color:var(--red)}.state.approval{color:var(--amber)}.detail{margin:0;padding:0 10px 9px 82px;color:var(--muted);font:11px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;word-break:break-word}
.side{display:flex;min-width:0;flex-direction:column}.section{padding:10px 12px}.section+.section{border-top:1px solid var(--line)}.label{margin-bottom:5px;color:var(--muted);font-size:10px;font-weight:650;letter-spacing:.08em;text-transform:uppercase}.target{min-height:46px}.target div{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.target .secondary{color:var(--muted);font-size:11px}.preview{display:grid;min-height:0;flex:1;place-items:center;overflow:hidden;background:#090b0f}.preview img{display:block;width:100%;height:100%;object-fit:contain}.preview-note{padding:12px;color:var(--muted);font-size:11px;text-align:center}
@media(prefers-color-scheme:light){:root{--card:#fff;--panel:#fafbfc;--line:#e1e5ea;--text:#1b1e24;--muted:#717986;--green:#168444;--red:#c83243;--amber:#996000;--blue:#246cba}.row{border-bottom-color:#0000000b}.row.newest summary{background:#6757c909}.preview{background:#f2f4f7}}
@media(max-width:520px){.body{grid-template-columns:1fr;height:390px}.timeline{height:230px;border-right:0;border-bottom:1px solid var(--line)}.side{height:160px}.preview{display:none}.row summary{grid-template-columns:56px minmax(0,1fr) 68px}.detail{padding-left:74px}}
</style></head><body><main class="card" id="card">
<header><div class="brand">DevMesh Live</div><div class="identity reconnecting" id="identity"><span class="dot"></span><span id="identityText">Preparing secure stream…</span></div></header>
<div class="body"><section class="timeline" id="timeline"><div class="empty" id="empty">Waiting for downstream MCP activity…</div></section><aside class="side"><section class="section target"><div class="label">Current target</div><div id="targetMain">—</div><div class="secondary" id="targetSecondary"></div></section><section class="preview" id="preview"><div class="preview-note">Latest preview will appear here</div></section></aside></div>
</main><script>
const $=id=>document.getElementById(id);const state={config:null,controller:null,lastId:0,attempt:0,rows:[],previewUrl:null,stopped:false};
function resize(){requestAnimationFrame(()=>window.openai?.notifyIntrinsicHeight?.(document.documentElement.scrollHeight))}
function metaFrom(value){return value?._meta?.devmeshLive||value?.devmeshLive||value?.toolResponseMetadata?.devmeshLive||null}
function accept(value){const meta=metaFrom(value);if(meta?.capability&&meta?.streamUrl){state.config=meta;connect()}}
function identity(status,text){const root=$('identity');root.className='identity '+status;const name=state.config?.serverName||'downstream MCP',session=state.config?.session;$('identityText').textContent=text||(name+(session?' · '+session:''))}
function statusOf(event){if(event.phase==='started')return['running','● running'];if(event.phase==='approval_required')return['approval','◆ approval'];if(event.phase==='failed'||event.status==='error')return['failed','✕ failed'];return['success','✓ '+(event.duration_ms!=null?event.duration_ms+' ms':'done')]}
function compactDetail(event){const out={};if(event.summary)out.summary=event.summary;if(event.details&&Object.keys(event.details).length)out.details=event.details;return JSON.stringify(out,null,2)}
function renderEvent(event){state.rows.forEach(row=>row.classList.remove('newest'));const row=document.createElement('details');row.className='row newest';const summary=document.createElement('summary'),time=document.createElement('span'),tool=document.createElement('span'),status=document.createElement('span');time.className='time';time.textContent=new Date(event.timestamp).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit'});tool.className='tool';tool.textContent=event.tool||event.source||'activity';tool.title=tool.textContent;const st=statusOf(event);status.className='state '+st[0];status.textContent=st[1];summary.append(time,tool,status);const detail=compactDetail(event);row.append(summary);if(detail!=='{}'){const pre=document.createElement('pre');pre.className='detail';pre.textContent=detail;row.append(pre)}$('empty')?.remove();$('timeline').prepend(row);state.rows.unshift(row);while(state.rows.length>75)state.rows.pop().remove();updateTarget(event.target);if(event.preview)loadPreview(event.preview.id);resize()}
function updateTarget(target){if(!target)return;const main=target.app||target.window_title||target.tab_title||target.semantic_target||'—';const rest=[target.window_title,target.tab_title,target.semantic_target].filter((v,i,a)=>v&&v!==main&&a.indexOf(v)===i);$('targetMain').textContent=main;$('targetSecondary').textContent=rest.join(' · ')}
async function loadPreview(id){if(!state.config)return;try{const response=await fetch(state.config.previewBaseUrl+'/'+encodeURIComponent(id),{headers:{Authorization:'Bearer '+state.config.capability},signal:state.controller?.signal});if(!response.ok)return;const blob=await response.blob(),url=URL.createObjectURL(blob),img=document.createElement('img');img.alt='Latest downstream preview';img.src=url;img.onload=()=>{if(state.previewUrl)URL.revokeObjectURL(state.previewUrl);state.previewUrl=url;$('preview').replaceChildren(img)}}catch(error){if(error.name!=='AbortError')console.debug('DevMesh preview unavailable') }}
function parseBlock(block){let id=null,eventName='message',data='';for(const line of block.split('\n')){if(line.startsWith('id:'))id=Number(line.slice(3).trim());else if(line.startsWith('event:'))eventName=line.slice(6).trim();else if(line.startsWith('data:'))data+=line.slice(5).trim()}if(id)state.lastId=id;if(eventName==='capability_expired'){identity('expired','Capability expired');state.stopped=true;return}if(data){try{renderEvent(JSON.parse(data))}catch(_){}}}
async function connect(){if(!state.config||state.controller||state.stopped)return;state.controller=new AbortController();identity(state.attempt?'reconnecting':'reconnecting',state.attempt?'Reconnecting…':'Connecting…');try{const headers={Authorization:'Bearer '+state.config.capability,Accept:'text/event-stream'};if(state.lastId)headers['Last-Event-ID']=String(state.lastId);const response=await fetch(state.config.streamUrl,{headers,cache:'no-store',signal:state.controller.signal});if(response.status===401||response.status===403){identity('expired','Capability expired');state.stopped=true;return}if(!response.ok||!response.body)throw new Error('stream '+response.status);state.attempt=0;identity('connected');const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';while(true){const part=await reader.read();if(part.done)break;buffer+=decoder.decode(part.value,{stream:true}).replace(/\r\n/g,'\n');let at;while((at=buffer.indexOf('\n\n'))>=0){const block=buffer.slice(0,at);buffer=buffer.slice(at+2);if(block&&!block.startsWith(':'))parseBlock(block)}}if(!state.stopped)throw new Error('stream ended')}catch(error){if(error.name==='AbortError'||state.stopped)return;identity('reconnecting','Reconnecting…');state.attempt++;const delay=Math.min(15000,750*Math.pow(2,Math.min(state.attempt,5)))+Math.random()*300;setTimeout(()=>{state.controller=null;connect()},delay)}finally{if(state.controller?.signal.aborted)state.controller=null}}
window.addEventListener('message',event=>{if(event.source!==window.parent)return;const message=event.data;if(message?.method==='ui/notifications/tool-result')accept(message.params)},{passive:true});
window.addEventListener('openai:set_globals',event=>{const globals=event.detail?.globals||event.detail||{};accept(globals.toolResponseMetadata||globals.toolOutput||globals)},{passive:true});
window.addEventListener('pagehide',()=>{state.stopped=true;state.controller?.abort();if(state.previewUrl)URL.revokeObjectURL(state.previewUrl)},{once:true});
accept(window.openai?.toolResponseMetadata||window.openai?.toolOutput);new ResizeObserver(resize).observe(document.body);resize();
</script></body></html>'''


def live_widget_resource(connect_url: str) -> dict:
    parsed = urlparse(connect_url)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else connect_url.rstrip("/")
    return {
        "uri": LIVE_WIDGET_URI,
        "name": "DevMesh live activity",
        "title": "DevMesh Live",
        "description": "A compact live console for authorized downstream MCP activity.",
        "mimeType": LIVE_WIDGET_MIME,
        "_meta": {
            "ui": {
                "prefersBorder": False,
                "csp": {"connectDomains": [origin], "resourceDomains": [origin]},
            },
            "openai/widgetCSP": {"connect_domains": [origin], "resource_domains": [origin]},
        },
    }
