#!/usr/bin/env python3
"""
Mermaid PDF editor with HTML sidebar controls and mouse-resize handles.
"""

import html
import json
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional

import markdown
import tkinter as tk
from tkinter import filedialog, messagebox

from md2pdf_converter import (
    MarkdownToPDFConverter,
    _clamp_scale,
    _get_mermaid_js,
    _normalize_transform,
)


PAGE_SIZES = {
    "A3": (1122, 1587),
    "A4": (794, 1123),
    "A5": (559, 794),
    "LETTER": (816, 1056),
    "LEGAL": (816, 1344),
}

PRESETS = [0.8, 1.0, 1.2, 1.5]

BG = "#1e1e2e"
TEXT = "#e0e0f0"
TEXT_DIM = "#888aaa"


def _extract_mermaid(md_text: str) -> List[str]:
    pattern = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL)
    return [m.group(1).strip() for m in pattern.finditer(md_text)]


def _first_line(code: str) -> str:
    return next((ln.strip() for ln in code.splitlines() if ln.strip()), "(empty)")


def _mermaid_kind(code: str) -> str:
    first = ""
    for line in code.splitlines():
        s = line.strip()
        if s:
            first = s.lower()
            break
    if first.startswith("gantt"):
        return "gantt"
    if first.startswith("graph") or first.startswith("flowchart"):
        return "flowchart"
    if first.startswith("sequence"):
        return "sequence"
    return "other"


class MermaidEditorWindow(tk.Toplevel):
    def __init__(
        self,
        master,
        md_path: str,
        output_dir: str,
        page_format: str = "A4",
        default_scale: float = 1.0,
    ):
        super().__init__(master)
        self.title("PDF Mermaid Editor")
        self.geometry("760x260")
        self.minsize(700, 220)
        self.configure(bg=BG)

        self.md_path = str(Path(md_path))
        self.output_dir = output_dir or str(Path(md_path).parent)
        self.page_format = (page_format or "A4").upper()
        self.default_scale = _clamp_scale(default_scale, 1.0)
        self.profile_path = str(Path(self.md_path).with_suffix(".mermaid-scales.json"))

        self.status_var = tk.StringVar(value="Initializing editor...")
        self._export_thread = None
        self._state_lock = threading.Lock()
        self._state_version = 0
        self._preview_server = None
        self._preview_thread = None
        self._preview_url = None

        self.md_text = Path(self.md_path).read_text(encoding="utf-8")
        self.diagrams = _extract_mermaid(self.md_text)
        self.titles = [_first_line(code) for code in self.diagrams]

        if not self.diagrams:
            messagebox.showinfo(
                "No Mermaid",
                "No mermaid blocks were found in selected markdown file.",
                parent=self,
            )
            self.destroy()
            return

        self.transforms: Dict[int, Dict[str, float]] = {
            i: {"x": self.default_scale, "y": self.default_scale}
            for i in range(len(self.diagrams))
        }

        self._build_shell_ui()
        self._bind_standard_shortcuts()
        self._load_profile(silent=True)
        self._start_preview_server()
        self._open_preview()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----- shell ui -----
    def _build_shell_ui(self):
        root = tk.Frame(self, bg=BG, padx=14, pady=14)
        root.pack(fill=tk.BOTH, expand=True)

        tk.Label(root, text="Mermaid editor is running", fg=TEXT, bg=BG,
                 font=("Segoe UI", 15, "bold")).pack(anchor="w")
        tk.Label(root, text=f"File: {self.md_path}", fg=TEXT_DIM, bg=BG,
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 0))
        tk.Label(root, text=f"Diagrams: {len(self.diagrams)}", fg=TEXT_DIM, bg=BG,
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(1, 0))

        row = tk.Frame(root, bg=BG)
        row.pack(anchor="w", pady=(12, 0))
        tk.Button(row, text="Open preview", command=self._open_preview).pack(side=tk.LEFT)
        self.export_btn = tk.Button(row, text="Save PDF", command=self._export_pdf)
        self.export_btn.pack(side=tk.LEFT, padx=(8, 0))
        tk.Button(row, text="Close", command=self._on_close).pack(side=tk.LEFT, padx=(8, 0))

        tk.Label(root, textvariable=self.status_var, fg=TEXT_DIM, bg=BG,
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(12, 0))

    def _bind_standard_shortcuts(self):
        for seq, virt in [
            ("<Control-c>", "<<Copy>>"),
            ("<Control-C>", "<<Copy>>"),
            ("<Control-v>", "<<Paste>>"),
            ("<Control-V>", "<<Paste>>"),
            ("<Control-x>", "<<Cut>>"),
            ("<Control-X>", "<<Cut>>"),
            ("<Control-z>", "<<Undo>>"),
            ("<Control-Z>", "<<Undo>>"),
        ]:
            self.bind(
                seq,
                lambda e, v=virt: self._dispatch_edit_shortcut(v),
                add="+",
            )

    def _dispatch_edit_shortcut(self, virt_event: str):
        widget = self.focus_get()
        if not widget:
            return
        try:
            widget.event_generate(virt_event)
            return "break"
        except Exception:
            return

    # ----- state -----
    def _touch_state(self):
        with self._state_lock:
            self._state_version += 1

    def _snapshot_state(self) -> Dict:
        with self._state_lock:
            transforms = {
                str(i): {"x": round(v["x"], 4), "y": round(v["y"], 4)}
                for i, v in self.transforms.items()
            }
            version = self._state_version
        return {
            "version": version,
            "titles": self.titles,
            "transforms": transforms,
            "presets": PRESETS,
        }

    def _set_transform(self, idx: int, x: float, y: float):
        if idx < 0 or idx >= len(self.diagrams):
            return
        with self._state_lock:
            self.transforms[idx] = {
                "x": _clamp_scale(x, self.default_scale),
                "y": _clamp_scale(y, self.default_scale),
            }
            self._state_version += 1

    def _set_many(self, mapping: Dict):
        with self._state_lock:
            for k, raw in mapping.items():
                try:
                    idx = int(k)
                except (TypeError, ValueError):
                    continue
                if idx < 0 or idx >= len(self.diagrams):
                    continue
                tr = _normalize_transform(raw, self.default_scale)
                self.transforms[idx] = {"x": tr["x"], "y": tr["y"]}
            self._state_version += 1

    # ----- profile -----
    def _save_profile(self):
        payload = {
            "source_markdown": self.md_path,
            "page_format": self.page_format,
            "diagram_transforms": {
                str(i): {"x": v["x"], "y": v["y"]} for i, v in self.transforms.items()
            },
            "diagram_scales": {
                str(i): round((v["x"] + v["y"]) / 2.0, 4)
                for i, v in self.transforms.items()
            },
        }
        Path(self.profile_path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.status_var.set(f"Profile saved: {self.profile_path}")

    def _load_profile(self, silent: bool = False) -> bool:
        p = Path(self.profile_path)
        if not p.exists():
            if not silent:
                self.status_var.set(f"Profile not found: {self.profile_path}")
            return False
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            raw = None
            if isinstance(data, dict):
                if isinstance(data.get("diagram_transforms"), dict):
                    raw = data.get("diagram_transforms")
                elif isinstance(data.get("diagram_scales"), dict):
                    raw = {k: {"x": v, "y": v} for k, v in data["diagram_scales"].items()}
            if raw is None and isinstance(data, dict):
                raw = data
            if isinstance(raw, dict):
                self._set_many(raw)
            self.status_var.set(f"Profile loaded: {self.profile_path}")
            return True
        except Exception as exc:
            if not silent:
                self.status_var.set(f"Profile load error: {exc}")
            return False

    # ----- html preview -----
    def _build_preview_html(self) -> str:
        pattern = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL)
        blocks: List[str] = []

        def repl(m):
            idx = len(blocks)
            code = m.group(1).strip()
            kind = _mermaid_kind(code)
            blocks.append(code)
            return (
                f'<div class=\"ditem\" data-id=\"{idx}\" data-kind=\"{kind}\"><div class=\"canvas\" data-id=\"{idx}\" data-kind=\"{kind}\">'
                f'<div class=\"mermaid\">{html.escape(code)}</div>'
                f'<div class=\"h hx\" data-dir=\"x\"></div>'
                f'<div class=\"h hy\" data-dir=\"y\"></div>'
                f'<div class=\"h hxy\" data-dir=\"xy\"></div>'
                f"</div></div>"
            )

        md_html_src = pattern.sub(repl, self.md_text)
        body_html = markdown.markdown(
            md_html_src,
            extensions=["tables", "fenced_code", "codehilite", "nl2br", "sane_lists"],
        )
        w_px, _ = PAGE_SIZES.get(self.page_format, PAGE_SIZES["A4"])
        template = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
*{box-sizing:border-box}body{margin:0;background:#232733;font-family:Segoe UI,Arial,sans-serif}
.layout{display:grid;grid-template-columns:320px 1fr;min-height:100vh}
.side{background:#1c2030;color:#e3e7f4;padding:12px;position:sticky;top:0;height:100vh;overflow:auto;border-right:1px solid #2f3548}
.blk{background:#242a3d;border:1px solid #2f3548;border-radius:8px;padding:10px;margin-bottom:10px}
.list{max-height:240px;overflow:auto;background:#1b2031;border:1px solid #313853;border-radius:6px}
.li{padding:7px;border-bottom:1px solid #2c3350;cursor:pointer;font-size:12px}.li:last-child{border-bottom:0}.li.act{background:#4f65b8}
.row{display:flex;gap:8px;align-items:center;margin-top:8px} .row label{width:20px;font-size:12px}
input[type=number]{width:100%;background:#161b2a;color:#e3e7f4;border:1px solid #394262;border-radius:4px;padding:6px}
.btn{border:0;border-radius:6px;padding:7px 9px;background:#303851;color:#e3e7f4;font-size:12px;cursor:pointer}
.btn:hover{background:#3b4564} .pres{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px}
.stage{padding:24px;display:flex;justify-content:center;overflow:auto}
.paper{width:__WPX__px;max-width:100%;background:#fff;box-shadow:0 10px 30px rgba(0,0,0,.22);padding:24px 56px 56px}
.paper>:first-child{margin-top:0!important} h1{font-size:1.9em;border-bottom:2px solid #2980b9;padding-bottom:.3em;margin:1.4em 0 .5em;color:#1a252f} h2{font-size:1.5em;border-bottom:1px solid #bdc3c7;padding-bottom:.2em;margin:1.3em 0 .4em;color:#2c3e50} h3{font-size:1.2em;margin:1.2em 0 .4em;color:#2c3e50} p{margin:.7em 0;line-height:1.65;text-align:justify} ul,ol{margin:.7em 0 .7em 1.8em} table{border-collapse:collapse;width:100%;margin:1em 0;font-size:.92em} th,td{border:1px solid #ddd;padding:7px 10px} th{background:#2980b9;color:#fff}
.ditem{margin:1.2em auto;padding:10px;border:1px dashed #d2dbe8;border-radius:6px;background:#fbfdff;overflow:auto}
.ditem.act{border-color:#5e84f0;box-shadow:0 0 0 2px rgba(94,132,240,.2)}
.canvas{position:relative;display:inline-block;min-width:40px;min-height:40px} .canvas svg{display:block;margin:0 auto;max-width:none;max-height:none}
.h{position:absolute;background:#4f79eb;border-radius:2px;z-index:10} .hx{right:-4px;top:50%;transform:translateY(-50%);width:8px;height:42px;cursor:ew-resize} .hy{left:50%;bottom:-4px;transform:translateX(-50%);width:42px;height:8px;cursor:ns-resize} .hxy{right:-6px;bottom:-6px;width:12px;height:12px;cursor:nwse-resize}
</style></head><body>
<div class="layout"><aside class="side">
<h3 style="margin:0 0 8px">Mermaid Editor</h3>
<div style="font-size:12px;color:#9ca7c7;margin-bottom:8px">Drag handles: right=width, bottom=height, corner=diagonal.</div>
<div class="blk"><div style="font-size:12px;margin-bottom:6px">Diagrams</div><div id="list" class="list"></div></div>
<div class="blk"><div style="font-size:12px;margin-bottom:6px">Selected size</div>
<div class="row"><label>X</label><input id="sx" type="number" min="0.5" max="2" step="0.01"></div>
<div class="row"><label>Y</label><input id="sy" type="number" min="0.5" max="2" step="0.01"></div>
<div class="row"><button class="btn" id="apply">Apply</button><button class="btn" id="rsel">Reset</button></div></div>
<div class="blk"><div style="font-size:12px;margin-bottom:6px">Presets</div><div id="pres" class="pres"></div><div class="row"><button class="btn" id="rall">Reset all</button></div></div>
<div class="blk"><div style="font-size:12px;margin-bottom:6px">Profile</div><div class="row"><button class="btn" id="savep">Save</button><button class="btn" id="loadp">Load</button></div></div>
<div id="st" style="font-size:12px;color:#93a2cd"></div>
</aside><main class="stage"><div class="paper">__BODY__</div></main></div>
<script src="/mermaid.js"></script>
<script>
const clamp=(v,d=1)=>{const n=Number(v);if(!Number.isFinite(n))return d;return Math.min(2,Math.max(0.5,n));};
const s={ver:-1,titles:[],tr:{},pres:[0.8,1,1.2,1.5],sel:0,drag:null};
const $=(q)=>document.querySelector(q), $$=(q)=>Array.from(document.querySelectorAll(q));
const dom={list:$('#list'),sx:$('#sx'),sy:$('#sy'),apply:$('#apply'),rsel:$('#rsel'),rall:$('#rall'),pres:$('#pres'),savep:$('#savep'),loadp:$('#loadp'),st:$('#st')};
const tr=(id)=>{const r=s.tr[String(id)]||{x:1,y:1};return{x:clamp(r.x),y:clamp(r.y)};};
const setTr=(id,x,y)=>s.tr[String(id)]={x:clamp(x),y:clamp(y)};
const canvas=(id)=>document.querySelector('.canvas[data-id="'+id+'"]');
const kind=(id)=>{const c=canvas(id);return c&&c.dataset.kind?c.dataset.kind:'other';};
function ensureBase(id){const c=canvas(id);if(!c)return false;if(c.dataset.bw&&c.dataset.bh)return true;const svg=c.querySelector('svg');if(!svg)return false;const vb=svg.viewBox&&svg.viewBox.baseVal?svg.viewBox.baseVal:null;if(vb&&vb.width>0&&vb.height>0){c.dataset.bw=String(vb.width);c.dataset.bh=String(vb.height);return true;}const r=svg.getBoundingClientRect();if(r.width>0&&r.height>0){c.dataset.bw=String(r.width);c.dataset.bh=String(r.height);return true;}return false;}
function applyGanttShrink(svg,sx,sy){
  const textScale=Math.max(0.65,Math.min(sx,sy));
  svg.querySelectorAll('text').forEach(t=>{
    if(!t.dataset.ofs){
      const fs=parseFloat(getComputedStyle(t).fontSize||'12');
      t.dataset.ofs=String(Number.isFinite(fs)&&fs>0?fs:12);
    }
    const base=parseFloat(t.dataset.ofs||'12');
    t.style.fontSize=Math.max(8,base*textScale).toFixed(2)+'px';
  });
  svg.querySelectorAll('rect.task, rect.task0, rect.task1, rect.active, rect.done, rect.crit, rect.milestone').forEach(r=>{
    if(!r.dataset.ox){r.dataset.ox=r.getAttribute('x')||'0';}
    if(!r.dataset.oy){r.dataset.oy=r.getAttribute('y')||'0';}
    if(!r.dataset.ow){r.dataset.ow=r.getAttribute('width')||'0';}
    if(!r.dataset.oh){r.dataset.oh=r.getAttribute('height')||'0';}
    const ox=parseFloat(r.dataset.ox||'0'),oy=parseFloat(r.dataset.oy||'0');
    const ow=parseFloat(r.dataset.ow||'0'),oh=parseFloat(r.dataset.oh||'0');
    if(ow>0){const nw=Math.max(1,ow*sx);r.setAttribute('x',String(ox+(ow-nw)/2));r.setAttribute('width',String(nw));}
    if(oh>0){const nh=Math.max(1,oh*sy);r.setAttribute('y',String(oy+(oh-nh)/2));r.setAttribute('height',String(nh));}
  });
}
function applyOne(id){
  if(!ensureBase(id))return;
  const c=canvas(id),svg=c?c.querySelector('svg'):null;if(!c||!svg)return;
  const bw=Number(c.dataset.bw||0),bh=Number(c.dataset.bh||0);if(!bw||!bh)return;
  const t=tr(id),k=kind(id);
  let lx=t.x,ly=t.y,sx=1,sy=1;
  if(k==='gantt'){
    const minX=0.9,minY=0.9;
    lx=Math.max(t.x,minX);ly=Math.max(t.y,minY);
    sx=t.x/lx;sy=t.y/ly;
  }
  svg.style.width=Math.max(40,bw*lx).toFixed(2)+'px';
  svg.style.height=Math.max(40,bh*ly).toFixed(2)+'px';
  svg.style.maxWidth='none';svg.style.maxHeight='none';
  if(k==='gantt'){applyGanttShrink(svg,sx,sy);}
}
function renderList(){dom.list.innerHTML='';s.titles.forEach((t,i)=>{const tt=(t||'').length>52?(t||'').slice(0,52)+'...':(t||'');const r=tr(i);const el=document.createElement('div');el.className='li'+(i===s.sel?' act':'');el.innerHTML='<div>#'+String(i+1).padStart(2,'0')+' '+tt+'</div><div style="opacity:.8">X '+r.x.toFixed(2)+' · Y '+r.y.toFixed(2)+'</div>';el.onclick=()=>select(i,true);dom.list.appendChild(el);});}
function syncInputs(){const t=tr(s.sel);dom.sx.value=t.x.toFixed(2);dom.sy.value=t.y.toFixed(2);}
function select(id,scroll=true){s.sel=Math.max(0,id);$$('.ditem').forEach(e=>e.classList.toggle('act',Number(e.dataset.id)===s.sel));renderList();syncInputs();if(scroll){const it=document.querySelector('.ditem[data-id="'+s.sel+'"]');if(it)it.scrollIntoView({behavior:'smooth',block:'center'});}}
async function api(path,p={}){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});return r.json();}
let pt=null;function pushSel(ms=80){clearTimeout(pt);pt=setTimeout(async()=>{const t=tr(s.sel);try{await api('/api/update-transform',{id:s.sel,x:t.x,y:t.y});}catch(_e){}},ms);}
function applyAll(){$$('.ditem').forEach(el=>applyOne(Number(el.dataset.id)));renderList();syncInputs();}
function bind(){dom.apply.onclick=()=>{setTr(s.sel,dom.sx.value,dom.sy.value);applyOne(s.sel);renderList();pushSel(0);};dom.sx.onkeydown=(e)=>{if(e.key==='Enter')dom.apply.onclick();};dom.sy.onkeydown=(e)=>{if(e.key==='Enter')dom.apply.onclick();};dom.rsel.onclick=async()=>{setTr(s.sel,1,1);applyOne(s.sel);renderList();syncInputs();await api('/api/update-transform',{id:s.sel,x:1,y:1});};dom.rall.onclick=async()=>{const m={};s.titles.forEach((_,i)=>m[String(i)]={x:1,y:1});s.tr=m;applyAll();await api('/api/set-transforms',{transforms:m});};dom.savep.onclick=async()=>{const r=await api('/api/save-profile');dom.st.textContent=r.ok?'Profile saved':'Save error';};dom.loadp.onclick=async()=>{const r=await api('/api/load-profile');if(r.ok&&r.state){s.tr=r.state.transforms||s.tr;applyAll();dom.st.textContent='Profile loaded';}else dom.st.textContent='Load error';};document.addEventListener('click',(e)=>{const it=e.target.closest('.ditem');if(it)select(Number(it.dataset.id),false);});}
function setupPresets(){dom.pres.innerHTML='';(s.pres||[0.8,1,1.2,1.5]).forEach(p=>{const b=document.createElement('button');b.className='btn';b.textContent=Math.round(p*100)+'%';b.onclick=()=>{setTr(s.sel,p,p);applyOne(s.sel);renderList();syncInputs();pushSel(0);};dom.pres.appendChild(b);});}
function setupDrag(){document.addEventListener('mousedown',(ev)=>{const h=ev.target.closest('.h');if(!h)return;const c=h.closest('.canvas');if(!c)return;const id=Number(c.dataset.id);if(!Number.isFinite(id)||!ensureBase(id))return;ev.preventDefault();select(id,false);const dir=h.dataset.dir||'xy';const t=tr(id);const bw=Number(c.dataset.bw||1),bh=Number(c.dataset.bh||1);s.drag={id,dir,sx:ev.clientX,sy:ev.clientY,bw,bh,w:bw*t.x,h:bh*t.y};document.body.style.userSelect='none';});document.addEventListener('mousemove',(ev)=>{const d=s.drag;if(!d)return;const dx=ev.clientX-d.sx,dy=ev.clientY-d.sy;let w=d.w,h=d.h;if(d.dir==='x'||d.dir==='xy')w=Math.max(40,d.w+dx);if(d.dir==='y'||d.dir==='xy')h=Math.max(40,d.h+dy);setTr(d.id,w/d.bw,h/d.bh);applyOne(d.id);renderList();syncInputs();pushSel(60);});document.addEventListener('mouseup',async()=>{if(!s.drag)return;const id=s.drag.id;s.drag=null;document.body.style.userSelect='';clearTimeout(pt);const t=tr(id);try{await api('/api/update-transform',{id:id,x:t.x,y:t.y});}catch(_e){}});}
async function pull(){try{const r=await fetch('/state',{cache:'no-store'});if(!r.ok)return;const p=await r.json();const first=s.ver<0;if(first||p.version!==s.ver){s.ver=p.version;s.titles=Array.isArray(p.titles)?p.titles:[];s.pres=Array.isArray(p.presets)?p.presets:s.pres;if(p.transforms&&typeof p.transforms==='object')s.tr=p.transforms;if(first){setupPresets();renderList();select(0,false);}applyAll();}}catch(_e){}}
async function init(){mermaid.initialize({startOnLoad:true,theme:'default',securityLevel:'loose',flowchart:{useMaxWidth:false,curve:'basis'},sequence:{useMaxWidth:false},gantt:{useMaxWidth:false,barHeight:22,barGap:6,topPadding:55,leftPadding:110,rightPadding:40,gridLineStartPadding:110,fontSize:'13px'}});bind();setupDrag();await pull();setInterval(pull,700);dom.st.textContent='Ready';}
window.addEventListener('load',()=>setTimeout(init,250));
</script></body></html>"""
        return template.replace("__WPX__", str(w_px)).replace("__BODY__", body_html)

    # ----- server -----
    def _start_preview_server(self):
        preview_html = self._build_preview_html().encode("utf-8")
        mermaid_js = _get_mermaid_js().encode("utf-8")
        editor = self

        class Handler(BaseHTTPRequestHandler):
            def _json(self, payload: Dict, status: int = 200):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_json(self) -> Dict:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                raw = self.rfile.read(length) if length > 0 else b"{}"
                try:
                    return json.loads(raw.decode("utf-8"))
                except Exception:
                    return {}

            def do_GET(self):
                p = self.path.split("?", 1)[0]
                if p in ("/", "/preview"):
                    body = preview_html
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if p == "/mermaid.js":
                    body = mermaid_js
                    self.send_response(200)
                    self.send_header("Content-Type", "application/javascript; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if p == "/state":
                    self._json(editor._snapshot_state())
                    return
                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                p = self.path.split("?", 1)[0]
                data = self._read_json()
                if p == "/api/update-transform":
                    idx = int(data.get("id", -1))
                    editor._set_transform(idx, data.get("x", 1.0), data.get("y", 1.0))
                    self._json({"ok": True})
                    return
                if p == "/api/set-transforms":
                    raw = data.get("transforms", {})
                    editor._set_many(raw if isinstance(raw, dict) else {})
                    self._json({"ok": True})
                    return
                if p == "/api/save-profile":
                    try:
                        editor._save_profile()
                        self._json({"ok": True})
                    except Exception as exc:
                        self._json({"ok": False, "error": str(exc)}, status=500)
                    return
                if p == "/api/load-profile":
                    ok = editor._load_profile(silent=False)
                    self._json({"ok": ok, "state": editor._snapshot_state()})
                    return
                self._json({"ok": False, "error": "not_found"}, status=404)

            def log_message(self, _fmt, *_args):
                return

        self._preview_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._preview_url = f"http://127.0.0.1:{self._preview_server.server_port}/preview"
        self._preview_thread = threading.Thread(
            target=self._preview_server.serve_forever,
            daemon=True,
        )
        self._preview_thread.start()
        self._touch_state()
        self.status_var.set("Preview server started.")

    def _open_preview(self):
        if not self._preview_url:
            messagebox.showerror("Error", "Preview server is not running.", parent=self)
            return
        webbrowser.open(self._preview_url)
        self.status_var.set("Preview opened in browser.")

    # ----- export -----
    def _export_pdf(self):
        out_pdf = filedialog.asksaveasfilename(
            title="Save PDF",
            initialdir=self.output_dir,
            initialfile=Path(self.md_path).stem + ".pdf",
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
            parent=self,
        )
        if not out_pdf:
            return
        if self._export_thread and self._export_thread.is_alive():
            return

        self.export_btn.config(state=tk.DISABLED, text="Exporting...")
        self.status_var.set("Export started...")
        self._export_thread = threading.Thread(
            target=self._run_export,
            args=(out_pdf,),
            daemon=True,
        )
        self._export_thread.start()

    def _run_export(self, out_pdf: str):
        try:
            with self._state_lock:
                transforms = {
                    idx: {"x": v["x"], "y": v["y"]}
                    for idx, v in self.transforms.items()
                }
            ok = MarkdownToPDFConverter(
                page_format=self.page_format,
                mermaid_scale=1.0,
            ).convert(
                self.md_path,
                out_pdf,
                title=Path(self.md_path).stem,
                diagram_transforms=transforms,
            )
            self.after(0, self._on_export_done, ok, out_pdf, None)
        except Exception as exc:
            self.after(0, self._on_export_done, False, out_pdf, str(exc))

    def _on_export_done(self, ok: bool, out_pdf: str, err: Optional[str]):
        self.export_btn.config(state=tk.NORMAL, text="Save PDF")
        if ok:
            self.status_var.set(f"PDF saved: {out_pdf}")
            messagebox.showinfo("Done", f"PDF saved:\n{out_pdf}", parent=self)
        else:
            msg = err or "Export failed"
            self.status_var.set(f"Export error: {msg}")
            messagebox.showerror("Error", msg, parent=self)

    def _on_close(self):
        try:
            if self._preview_server:
                self._preview_server.shutdown()
                self._preview_server.server_close()
        except Exception:
            pass
        self.destroy()
