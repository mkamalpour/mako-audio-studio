"""Accessible local waveform board and delegated browser interactions."""
from __future__ import annotations
import html

CSS = """
#timeline-bridge {display:none !important}
#clip-board {border:1px solid #cbd5d1;border-radius:14px;background:#f5f8f7;padding:18px;color:#20332f}
.board-heading {display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:12px}
.board-heading strong {font-size:18px}.board-count {font-size:13px;color:#51645e}
.clip-lane {display:flex;gap:12px;overflow-x:auto;padding:6px 2px 14px;min-height:155px;align-items:stretch}
.clip-card {flex:0 0 220px;min-width:0;background:white;border:1px solid #c6d8d2;border-top:4px solid #147d70;border-radius:8px;padding:12px;cursor:grab;position:relative;box-shadow:0 2px 4px #183a3010}
.clip-card.reza {border-top-color:#a86e16}.clip-card.selected {outline:3px solid #147d70;outline-offset:2px}
.clip-card:focus-visible {outline:3px solid #0f766e;outline-offset:2px}.clip-card.dragging {opacity:.35}
.clip-card.missing {border-top-color:#b42318;background:#fff4f2}.clip-title {display:flex;justify-content:space-between;align-items:center;font-size:16px}.clip-speaker {font-size:12px;margin-top:3px;color:#53665e}
.clip-wave {height:42px;display:flex;align-items:center;gap:1px;margin:10px 0;color:#147d70}.reza .clip-wave {color:#a86e16}.clip-wave i {display:block;flex:1;background:currentColor;border-radius:1px;min-width:1px}
.clip-actions {display:flex;gap:4px}.clip-actions button,.board-toolbar button {border:1px solid #b6c9c1;border-radius:5px;background:#fff;color:#203d33;padding:5px 10px;cursor:pointer;min-height:34px}.clip-actions button:hover,.board-toolbar button:hover {background:#dceee7}.clip-actions button:focus-visible,.board-toolbar button:focus-visible {outline:3px solid #147d70}
.clip-actions button:disabled {opacity:.35;cursor:default}.clip-error {font-size:12px;color:#9b251a;overflow-wrap:anywhere}.board-toolbar {display:flex;gap:7px;flex-wrap:wrap;margin-bottom:10px}.board-empty {padding:24px;border:1px dashed #a9c4b8;border-radius:8px;align-self:center;color:#425c50}.board-pool {border-top:1px solid #ccdcd5;padding-top:14px;margin-top:8px}.board-help {font-size:12px;color:#4b6257;margin-top:10px}.clip-file {font-size:11px;color:#5b6c63;overflow-wrap:anywhere;margin-top:6px}
@media(max-width:600px){#clip-board{padding:12px}.clip-card{flex-basis:220px}.board-heading{align-items:flex-start;flex-direction:column}}
@media(prefers-color-scheme:dark){#clip-board{background:#172b26;color:#e4f1eb;border-color:#405d50}.clip-card{background:#213c32;color:#f3faf6;border-color:#486253}.clip-speaker,.board-count,.board-help,.clip-file{color:#c3d5cb}.clip-card.missing{background:#422721}.clip-error{color:#ffbab0}}
"""
JS = r"""() => {
if(window.makoBoardInstalled) return; window.makoBoardInstalled=true;
let dragged=null, pendingFocus=null, originalLane=null, originalCards=[];
function restoreDrag() {
 if(originalLane&&originalLane.isConnected)originalCards.forEach(card=>originalLane.appendChild(card));
 if(dragged)dragged.classList.remove('dragging');
 originalLane=null;originalCards=[];
}
new MutationObserver(()=>{
 if(!pendingFocus)return;
 const board=document.getElementById('clip-board');
 if(!board||board===pendingFocus.board)return;
 const card=[...board.querySelectorAll('.clip-card')].find(c=>c.dataset.token===pendingFocus.token);
 if(card){card.focus({preventScroll:true});card.scrollIntoView({behavior:'auto',block:'nearest',inline:'nearest'});}
 pendingFocus=null;
}).observe(document.body,{childList:true,subtree:true});
function emit(action, token, tokens) {
 const board=document.getElementById('clip-board');
 const box=document.querySelector('#timeline-bridge textarea');
 if(!board || !box) return;
 const data=JSON.stringify({action,token,tokens,section:board.dataset.section,context:board.dataset.context,revision:board.dataset.revision,nonce:Date.now()+Math.random()});
 const setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;
 setter.call(box,data); box.dispatchEvent(new Event('input',{bubbles:true}));
}
document.addEventListener('click',event=>{
 const board=event.target.closest('#clip-board');if(!board)return;
 const control=event.target.closest('[data-action]');
 const card=event.target.closest('[data-token]');
 if(control){event.preventDefault();emit(control.dataset.action,card?.dataset.token);}
 else if(card) emit('select',card.dataset.token);
});
document.addEventListener('keydown',event=>{
 const card=event.target.closest('#clip-board .clip-card');if(!card)return;
 if(event.target.tagName==='BUTTON')return;
 let action=null;
 if(event.altKey&&event.key==='ArrowLeft')action='up';
 if(event.altKey&&event.key==='ArrowRight')action='down';
 if(event.key==='Enter'||event.key===' ')action='select';
 if(action){event.preventDefault();pendingFocus={token:card.dataset.token,board:document.getElementById('clip-board')};emit(action,card.dataset.token);}
});
document.addEventListener('dragstart',event=>{
 const card=event.target.closest('#clip-board .clip-lane[data-ordered] .clip-card');if(!card)return;
 dragged=card;originalLane=card.parentElement;originalCards=[...originalLane.children];event.dataTransfer.setData('text/plain',card.dataset.token);event.dataTransfer.effectAllowed='move';card.classList.add('dragging');
});
document.addEventListener('dragover',event=>{
 const lane=event.target.closest('#clip-board .clip-lane[data-ordered]');if(!lane||!dragged)return;
 event.preventDefault();const target=event.target.closest('.clip-card');
 if(target&&target!==dragged){const r=target.getBoundingClientRect();lane.insertBefore(dragged,event.clientX<r.left+r.width/2?target:target.nextSibling);}
 else if(!target)lane.appendChild(dragged);
});
document.addEventListener('drop',event=>{
 const lane=event.target.closest('#clip-board .clip-lane[data-ordered]');if(!lane||!dragged)return;
 event.preventDefault();const token=dragged.dataset.token;const order=[...lane.querySelectorAll('.clip-card')].map(c=>c.dataset.token);
 restoreDrag();dragged=null;emit('reorder',token,order);
});
document.addEventListener('dragend',()=>{restoreDrag();dragged=null;});
}"""

def render_board(catalog, tokens, section, selected=None, context="", revision=""):
    esc=lambda value:html.escape(str(value),quote=True)
    known={item['token']:item for item in catalog}
    def card(token,index=None):
        item=known.get(token,{'token':token,'speaker':'koroush' if token.upper().startswith('K') else 'reza','error':'Missing prepared recording','peaks':[],'duration_s':0})
        error=item.get('error','')
        cls='clip-card '+item['speaker']+(' selected' if selected==token else '')+(' missing' if error else '')
        bars=''.join(f'<i style="height:{max(2,min(42,float(p)*42)):.1f}px"></i>' for p in item.get('peaks',[]))
        actions=(f'<button data-action="up" aria-label="Move {esc(token)} earlier" {"disabled" if index==0 else ""}>Earlier</button><button data-action="down" aria-label="Move {esc(token)} later" {"disabled" if index==len(tokens)-1 else ""}>Later</button><button data-action="remove" aria-label="Remove {esc(token)} from section">Remove</button>') if index is not None else f'<button data-action="add" aria-label="Add {esc(token)} to section">+ Add clip</button>'
        return f'<article class="{cls}" tabindex="0" draggable="{str(index is not None).lower()}" data-token="{esc(token)}" aria-label="{esc(token)} {esc(item["speaker"])}"><div class="clip-title"><strong>{esc(token)}</strong><span>{float(item.get("duration_s",0)):.1f}s</span></div><div class="clip-speaker">{esc(item["speaker"].capitalize())}{" / "+str(index+1) if index is not None else ""}</div><div class="clip-wave" aria-label="Audio amplitude waveform">{bars}</div>{f"<div class=clip-error>{esc(error)}</div>" if error else ""}<div class="clip-actions">{actions}</div><div class="clip-file">{esc(item.get("filename",""))}</div></article>'
    ordered=''.join(card(token,index) for index,token in enumerate(tokens)) or '<div class="board-empty">No clips in this section. Add a prepared recording below.</div>'
    pool=[item['token'] for item in catalog if item['token'] not in tokens]
    available=''.join(card(token) for token in pool)
    pool_markup = f'<div class="board-pool"><strong>Available clips</strong><div class="clip-lane">{available}</div></div>' if pool else '<div class="board-help">All prepared clips are included.</div>'
    return f'<section id="clip-board" data-section="{esc(section or "")}" data-context="{esc(context)}" data-revision="{esc(revision)}" aria-label="Visual section editor"><div class="board-heading"><strong>Section {esc(section or "-")} / Conversation board</strong><span class="board-count">{len(tokens)} clips / {sum(float(known.get(t,{}).get("duration_s",0)) for t in tokens):.1f}s of recordings</span></div><div class="board-toolbar"><button data-action="undo">Undo</button><button data-action="redo">Redo</button></div><div class="clip-lane" data-ordered="true">{ordered}</div><div class="board-help">Swipe sideways to browse / Drag to arrange / Select a card to listen below / Alt + Left / Right moves a focused card / Changes update your episode order; save to keep them.</div>{pool_markup}</section>'
