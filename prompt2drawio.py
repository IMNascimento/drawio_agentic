#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prompt -> .drawio nativo (sem imagens) — Especialista em UML/DER
- Modes: auto | er | class | sequence | state | activity | usecase | generic
- Usa estilos nativos do draw.io via styles.json (harvest).
- Auto-resolve estilos por nome (er.*, uml.*, usecase.*, edge.*), com override por CLI (--style-*).
- Gera layout em camadas (TD/LR) e salva com hash curto: <out>_<hash>.drawio (desligue com --no-hash).
"""

import os
import sys
import re
import json
import uuid
import time
import hashlib
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from collections import defaultdict, deque
import html as _html

import click

# ---------------- Spinner ----------------
class Spinner:
    FRAMES = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]
    def __init__(self, text: str):
        self.text=text
        self._stop=threading.Event()
        self._t=threading.Thread(target=self._run,daemon=True)
    def _run(self):
        i=0
        while not self._stop.is_set():
            sys.stdout.write(f"\r{self.FRAMES[i%len(self.FRAMES)]} {self.text}")
            sys.stdout.flush()
            i+=1; time.sleep(0.08)
        sys.stdout.write("\r"); sys.stdout.flush()
    def start(self): self._t.start()
    def stop(self,msg:Optional[str]=None):
        self._stop.set(); self._t.join()
        if msg: print(msg)

# ---------------- Config ----------------
OPENAI_MODEL_DEFAULT = "gpt-4o-mini"
TIMEOUT = 900
NODE_W, NODE_H = 240, 96
H_GAP, V_GAP = 200, 160

# ---------------- Utils ----------------
def html_escape(s: str) -> str:
    """Escapa conteúdo HTML que vai DENTRO do texto do label (antes de ir para atributo)."""
    return _html.escape(s or "", quote=True)

def xml_attr(s: Optional[str]) -> str:
    """Escapa para uso SEGURO em atributos XML (id, value, name, style, source, target)."""
    if s is None:
        return ""
    return (s.replace("&","&amp;")
             .replace("<","&lt;")
             .replace(">","&gt;")
             .replace('"',"&quot;"))

def strip_to_json(text: str) -> str:
    t=text.strip()
    if t.startswith("```"):
        t=t.strip("`")
        if t.lower().startswith("json"):
            t=t[4:].lstrip()
    s=t.find("{"); e=t.rfind("}")
    if s==-1 or e==-1 or e<=s: raise RuntimeError("LLM não retornou JSON válido.")
    return t[s:e+1]

def infer_mode(prompt: str) -> str:
    p=prompt.lower()
    if any(k in p for k in ["der","e-r","entidade","tabela","relacionamento","cardinalidade"]): return "er"
    if any(k in p for k in ["classe","uml class","class diagram"]): return "class"
    if any(k in p for k in ["sequência","sequence","lifeline","mensagem"]): return "sequence"
    if any(k in p for k in ["estado","state diagram","statechart"]): return "state"
    if any(k in p for k in ["atividade","activity","fluxo","flow","bpmn"]): return "activity"
    if any(k in p for k in ["use case","caso de uso","ator"]): return "usecase"
    return "generic"

# ---------------- LLM core ----------------
def llm_call(model: str, system: str, user: str, temperature: float=0.0) -> str:
    if model.startswith("ollama:"):
        import urllib.request, urllib.error, json as _json
        body={"model":model.split("ollama:",1)[1],"prompt":system+"\n\n"+user,"stream":False,"options":{"temperature":temperature}}
        data=_json.dumps(body).encode("utf-8")
        req=urllib.request.Request("http://localhost:11434/api/generate",data=data,headers={"Content-Type":"application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return _json.loads(resp.read().decode("utf-8")).get("response","")
    else:
        try:
            from openai import OpenAI
        except Exception as e:
            raise RuntimeError("Instale o SDK: pip install openai") from e
        api_key=os.getenv("OPENAI_API_KEY")
        if not api_key: raise RuntimeError("Defina OPENAI_API_KEY ou use --model ollama:<modelo>.")
        client=OpenAI(api_key=api_key)
        r=client.chat.completions.create(model=model,messages=[{"role":"system","content":system},{"role":"user","content":user}],temperature=temperature)
        return r.choices[0].message.content or ""

# ---------------- Prompts ---------------
GENERIC_SPEC = """Retorne APENAS JSON válido de grafo:
{
  "title": "opcional",
  "direction": "TD"|"LR",
  "nodes": [{"id":"n1","label":"Texto","shape":"rect|round|rhombus"}],
  "edges": [{"from":"n1","to":"n2","label":"opcional"}]
}
Regras:
- IDs curtas sem espaços; shape default rect; decisões rhombus; início/fim round.
- direction default "TD".
- Sem markdown/comentários fora do JSON.
"""

ER_SPEC = """Retorne APENAS JSON válido de DER:
{
  "title":"opcional",
  "direction":"TD"|"LR",
  "entities":[
    {"name":"Users","attributes":[
      {"name":"id","type":"uuid","pk":true,"unique":true,"nullable":false},
      {"name":"email","type":"varchar(255)","unique":true,"nullable":false}
    ]}
  ],
  "relations":[
    {"from":"Users.id","to":"Auth.user_id","cardinality":"1:N","name":"has_auth"}
  ]
}
Regras:
- Tipos, PK/UNIQUE/NULL (booleans).
- Cardinalidade: "1","0..1","1..*","*","1:N","N:1","N:M".
- Sem markdown/comentários fora do JSON.
"""

CLASS_SPEC = """Retorne APENAS JSON válido de diagrama de classes UML:
{
  "title":"opcional",
  "direction":"TD"|"LR",
  "classes":[
    {"name":"User",
     "attributes":[{ "visibility":"+|-|#","name":"email","type":"string"}],
     "methods":[{ "visibility":"+|-|#","signature":"resetPassword(token:string): bool"}]}
  ],
  "relations":[
    {"from":"User","to":"AuthService","type":"association|aggregation|composition|inheritance|dependency","label":"opcional"}
  ]
}
Regras:
- visibility default "+";
- type default "association";
- Sem markdown/comentários fora do JSON.
"""

SEQ_SPEC = """Retorne APENAS JSON válido de diagrama de sequência UML:
{
  "title":"opcional",
  "participants":["Client","API","DB"],
  "messages":[
    {"from":"Client","to":"API","label":"login()"},
    {"from":"API","to":"DB","label":"SELECT user"}
  ]
}
Sem markdown/comentários fora do JSON.
"""

STATE_SPEC = """Retorne APENAS JSON válido de diagrama de estados:
{
  "title":"opcional",
  "direction":"TD"|"LR",
  "states":["Idle","EnteringPIN","Locked"],
  "transitions":[{"from":"Idle","to":"EnteringPIN","label":"cardInserted"}],
  "start":"Idle",
  "end":"Locked"
}
Sem markdown/comentários fora do JSON.
"""

ACTIVITY_SPEC = """Retorne APENAS JSON válido de diagrama de atividades:
{
  "title":"opcional",
  "direction":"TD"|"LR",
  "activities":[{"id":"a1","label":"Start","kind":"start|action|decision|merge|end"}],
  "edges":[{"from":"a1","to":"a2","label":"condição opcional"}]
}
Sem markdown/comentários fora do JSON.
"""

USECASE_SPEC = """Retorne APENAS JSON válido de diagrama de casos de uso:
{
  "title":"opcional",
  "actors":["User","Admin"],
  "usecases":["Login","Reset Password"],
  "relations":[
    {"from":"User","to":"Login","type":"association"},
    {"from":"Login","to":"MFA","type":"include|extend","label":"include"}
  ]
}
Sem markdown/comentários fora do JSON.
"""

# ---------------- Load styles.json ----------------
def load_styles(styles_path: Optional[Path]) -> Dict[str,str]:
    if not styles_path: return {}
    p = Path(styles_path)
    if not p.exists(): raise RuntimeError(f"styles.json não encontrado: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("styles.json não é um objeto {key: style}")
        return {str(k): str(v) for k, v in data.items()}
    except Exception as e:
        raise RuntimeError(f"Falha lendo styles.json: {e}")

def style_from_key_or_literal(arg: Optional[str], styles: Dict[str,str]) -> Optional[str]:
    """Se começar com 'shape=' assume string de estilo; senão busca chave no styles.json."""
    if not arg: return None
    a = arg.strip()
    if a.lower().startswith("shape=") or ";" in a:
        return a
    return styles.get(a)

def _find_style(styles: Dict[str,str], include: List[str], exclude: List[str]=[]) -> Optional[str]:
    """Busca melhor estilo por chave no styles.json (case-insensitive; contém todos tokens include, e não contém exclude)."""
    if not styles: return None
    inc = [s.lower() for s in include]
    exc = [s.lower() for s in exclude]
    best_key=None
    for k in styles.keys():
        lk=k.lower()
        if all(tok in lk for tok in inc) and not any(tok in lk for tok in exc):
            best_key=k
            break
    return styles.get(best_key) if best_key else None

def ensure_html(style: str) -> str:
    return style if re.search(r'(^|;)html=1(;|$)', style) else (style.rstrip(';')+';html=1;')

# ---------------- Builders (parse + validação) ----------------
def build_generic(prompt:str, model:str)->Dict[str,Any]:
    text=llm_call(model, "Você retorna apenas JSON válido.", GENERIC_SPEC+"\n\nPrompt:\n"+prompt, 0.0)
    s=json.loads(strip_to_json(text))
    s.setdefault("title",""); s.setdefault("direction","TD"); s.setdefault("nodes",[]); s.setdefault("edges",[])
    for n in s["nodes"]:
        n.setdefault("shape","rect")
        if n["shape"] not in ("rect","round","rhombus"): n["shape"]="rect"
        n["label"]=str(n.get("label", n.get("id","")))
    for e in s["edges"]:
        if "from" not in e or "to" not in e: raise RuntimeError("Aresta sem from/to")
        e["label"]=str(e.get("label",""))
    if not s["nodes"]: s["nodes"]=[{"id":"n1","label":"Start","shape":"round"}]
    return s

def build_er(prompt:str, model:str)->Dict[str,Any]:
    text=llm_call(model, "Você retorna apenas JSON válido.", ER_SPEC+"\n\nPrompt:\n"+prompt, 0.0)
    d=json.loads(strip_to_json(text))
    d.setdefault("title",""); d.setdefault("direction","TD")
    ents=[]
    for e in d.get("entities",[]):
        attrs=[]
        for a in e.get("attributes",[]):
            attrs.append({"name":str(a.get("name","id")),
                          "type":str(a.get("type","text")),
                          "pk":bool(a.get("pk",False)),
                          "unique":bool(a.get("unique",False)),
                          "nullable":bool(a.get("nullable",True))})
        ents.append({"name":str(e.get("name","Entity")), "attributes":attrs})
    d["entities"]=ents
    rels=[]
    for r in d.get("relations",[]):
        if "from" in r and "to" in r:
            rels.append({"from":str(r["from"]), "to":str(r["to"]),
                         "cardinality":str(r.get("cardinality","1:N")),
                         "name":str(r.get("name",""))})
    d["relations"]=rels
    if not d["entities"]:
        d["entities"]=[{"name":"Entity","attributes":[{"name":"id","type":"uuid","pk":True,"unique":True,"nullable":False}]}]
    return d

def build_class(prompt:str, model:str)->Dict[str,Any]:
    text=llm_call(model, "Você retorna apenas JSON válido.", CLASS_SPEC+"\n\nPrompt:\n"+prompt, 0.0)
    d=json.loads(strip_to_json(text))
    d.setdefault("title",""); d.setdefault("direction","TD")
    cls=[]
    for c in d.get("classes",[]):
        attrs=[{"visibility":(a.get("visibility","+") or "+"),
                "name":str(a.get("name","attr")),
                "type":str(a.get("type",""))} for a in c.get("attributes",[])]
        meths=[{"visibility":(m.get("visibility","+") or "+"),
                "signature":str(m.get("signature","method(): void"))} for m in c.get("methods",[])]
        cls.append({"name":str(c.get("name","Class")), "attributes":attrs, "methods":meths})
    d["classes"]=cls
    rs=[]
    for r in d.get("relations",[]):
        if "from" in r and "to" in r:
            rs.append({"from":str(r["from"]), "to":str(r["to"]),
                       "type":str(r.get("type","association")),
                       "label":str(r.get("label",""))})
    d["relations"]=rs
    if not d["classes"]: d["classes"]=[{"name":"Class","attributes":[],"methods":[]}]
    return d

def build_sequence(prompt:str, model:str)->Dict[str,Any]:
    text=llm_call(model, "Você retorna apenas JSON válido.", SEQ_SPEC+"\n\nPrompt:\n"+prompt, 0.0)
    d=json.loads(strip_to_json(text))
    d.setdefault("title",""); d.setdefault("participants",[]); d.setdefault("messages",[])
    if not d["participants"]: d["participants"]=["A","B"]
    return d

def build_state(prompt:str, model:str)->Dict[str,Any]:
    text=llm_call(model, "Você retorna apenas JSON válido.", STATE_SPEC+"\n\nPrompt:\n"+prompt, 0.0)
    d=json.loads(strip_to_json(text))
    d.setdefault("title",""); d.setdefault("direction","TD")
    d.setdefault("states",[]); d.setdefault("transitions",[]); d.setdefault("start",None); d.setdefault("end",None)
    return d

def build_activity(prompt:str, model:str)->Dict[str,Any]:
    text=llm_call(model, "Você retorna apenas JSON válido.", ACTIVITY_SPEC+"\n\nPrompt:\n"+prompt, 0.0)
    d=json.loads(strip_to_json(text))
    d.setdefault("title",""); d.setdefault("direction","TD")
    d.setdefault("activities",[]); d.setdefault("edges",[])
    return d

def build_usecase(prompt:str, model:str)->Dict[str,Any]:
    text=llm_call(model, "Você retorna apenas JSON válido.", USECASE_SPEC+"\n\nPrompt:\n"+prompt, 0.0)
    d=json.loads(strip_to_json(text))
    d.setdefault("title",""); d.setdefault("actors",[]); d.setdefault("usecases",[]); d.setdefault("relations",[])
    return d

# ---------------- Converters (-> grafos genéricos) ----------------
def nodes_edges_er(d:Dict[str,Any])->Dict[str,Any]:
    nodes=[]; edges=[]
    name_to_id={}
    for i,e in enumerate(d["entities"],1):
        nid=f"E{i}"; name_to_id[e["name"]]=nid
        header=f"<b>{html_escape(e['name'])}</b>"
        rows=[]
        for a in e["attributes"]:
            flags=[]
            if a["pk"]: flags.append("PK")
            if a["unique"]: flags.append("UQ")
            if not a["nullable"]: flags.append("NOT NULL")
            flg=(" ("+", ".join(flags)+")") if flags else ""
            rows.append(f"{html_escape(a['name'])}: {html_escape(a['type'])}{flg}")
        body="<br/>".join(rows) if rows else "<i>(sem atributos)</i>"
        label=f'{header}<hr/>{body}'
        nodes.append({"id":nid,"label":label,"shape":"rect","html":True})
    def splitf(s:str)->Tuple[str,str]:
        return s.split(".",1) if "." in s else (s,"id")
    for r in d["relations"]:
        a,_=splitf(r["from"]); b,_=splitf(r["to"])
        if a in name_to_id and b in name_to_id:
            lbl=r.get("name","").strip()
            card=r.get("cardinality","").strip()
            final=(card if card and not lbl else (f"{lbl} ({card})" if lbl and card else lbl))
            edges.append({"from":name_to_id[a],"to":name_to_id[b],"label":final})
    return {"title":d["title"],"direction":d["direction"],"nodes":nodes,"edges":edges}

def nodes_edges_class(d:Dict[str,Any])->Dict[str,Any]:
    nodes=[]; edges=[]; name_to_id={}
    for i,c in enumerate(d["classes"],1):
        nid=f"C{i}"; name_to_id[c["name"]]=nid
        header=f"<b>{html_escape(c['name'])}</b>"
        attrs=[f"{html_escape(a['visibility'])} {html_escape(a['name'])}: {html_escape(a['type'])}".strip() for a in c["attributes"]]
        meths=[html_escape(m["visibility"])+" "+html_escape(m["signature"]) for m in c["methods"]]
        body1="<br/>".join(attrs) if attrs else "<i>(sem atributos)</i>"
        body2="<br/>".join(meths) if meths else "<i>(sem métodos)</i>"
        label=f"{header}<hr/>{body1}<hr/>{body2}"
        nodes.append({"id":nid,"label":label,"shape":"rect","html":True})
    for r in d["relations"]:
        if r["from"] in name_to_id and r["to"] in name_to_id:
            edges.append({"from":name_to_id[r["from"]],"to":name_to_id[r["to"]],
                          "label":r.get("label","") or r.get("type","association")})
    return {"title":d["title"],"direction":d.get("direction","TD"),"nodes":nodes,"edges":edges}

def nodes_edges_sequence(d:Dict[str,Any])->Dict[str,Any]:
    nodes=[{"id":f"P{i}","label":html_escape(p),"shape":"round","html":True} for i,p in enumerate(d["participants"],1)]
    edges=[{"from":f"P{d['participants'].index(m['from'])+1}",
            "to":f"P{d['participants'].index(m['to'])+1}",
            "label":m.get("label","")} for m in d["messages"] if m.get("from") in d["participants"] and m.get("to") in d["participants"]]
    return {"title":d["title"],"direction":"LR","nodes":nodes,"edges":edges}

def nodes_edges_state(d:Dict[str,Any])->Dict[str,Any]:
    nodes=[]; edges=[]; ids={}
    if d.get("start"): ids[d["start"]]="S0"; nodes.append({"id":"S0","label":"● Start","shape":"round","html":True})
    if d.get("end"):   ids[d["end"]]="SE"; nodes.append({"id":"SE","label":"■ End","shape":"rect","html":True})
    for s in d.get("states",[]):
        if s not in ids:
            nid=f"S{len(ids)+1}"; ids[s]=nid; nodes.append({"id":nid,"label":html_escape(s),"shape":"round","html":True})
    for t in d.get("transitions",[]):
        a=ids.get(t.get("from")); b=ids.get(t.get("to"))
        if a and b: edges.append({"from":a,"to":b,"label":t.get("label","")})
    return {"title":d["title"],"direction":d.get("direction","TD"),"nodes":nodes,"edges":edges}

def nodes_edges_activity(d:Dict[str,Any])->Dict[str,Any]:
    nodes=[]; edges=[]; idmap={}
    for i,a in enumerate(d["activities"],1):
        nid=f"A{i}"; idmap[a["id"]]=nid
        shape={"start":"round","end":"round","decision":"rhombus","merge":"rhombus"}.get(a.get("kind","action"),"rect")
        label=html_escape(a.get("label",a["id"]))
        nodes.append({"id":nid,"label":label,"shape":shape,"html":True})
    for e in d["edges"]:
        if e.get("from") in idmap and e.get("to") in idmap:
            edges.append({"from":idmap[e["from"]],"to":idmap[e["to"]],"label":e.get("label","")})
    return {"title":d["title"],"direction":d.get("direction","TD"),"nodes":nodes,"edges":edges}

# ---------------- Layout ----------------
def build_graph(spec: Dict[str,Any]) -> Tuple[Dict[str,Any], Dict[str,int]]:
    nodes_by_id={n["id"]:n for n in spec["nodes"]}
    indegree={nid:0 for nid in nodes_by_id}
    for e in spec["edges"]:
        if e["to"] in indegree: indegree[e["to"]]+=1
        else:
            nodes_by_id.setdefault(e["to"], {"id":e["to"],"label":e["to"],"shape":"rect","html":True})
            indegree[e["to"]]=1
        if e["from"] not in nodes_by_id:
            nodes_by_id.setdefault(e["from"], {"id":e["from"],"label":e["from"],"shape":"rect","html":True})
            indegree.setdefault(e["from"],0)
    return nodes_by_id, indegree

def choose_direction_auto(spec: Dict[str,Any]) -> str:
    nodes_by_id, indegree = build_graph(spec)
    adj=defaultdict(list)
    for e in spec["edges"]: adj[e["from"]].append(e["to"])
    q=deque([nid for nid,d in indegree.items() if d==0]) or deque([next(iter(nodes_by_id))])
    dist={nid:0 for nid in q}; vis=set(q)
    while q:
        u=q.popleft()
        for v in adj.get(u,[]):
            if v not in vis: dist[v]=dist[u]+1; vis.add(v); q.append(v)
    layers=defaultdict(int)
    for nid,d in dist.items(): layers[d]+=1
    return "LR" if (max(layers.values()) if layers else 1) > (len(layers) or 1) else "TD"

def layer_layout(spec: Dict[str,Any], direction: Optional[str]) -> Dict[str,Tuple[int,int]]:
    if not direction: spec["direction"]=choose_direction_auto(spec)
    else: spec["direction"]=direction
    nodes_by_id, indegree=build_graph(spec)
    adj=defaultdict(list)
    for e in spec["edges"]: adj[e["from"]].append(e["to"])
    q=deque([nid for nid,d in indegree.items() if d==0]) or deque([next(iter(nodes_by_id))])
    dist={nid:0 for nid in q}; vis=set(q)
    while q:
        u=q.popleft()
        for v in adj.get(u,[]):
            if v not in vis: dist[v]=dist[u]+1; vis.add(v); q.append(v)
    layers:Dict[int,List[str]]={}
    for nid,d in dist.items(): layers.setdefault(d,[]).append(nid)
    pos={}
    if spec["direction"]=="TD":
        for layer,nids in sorted(layers.items()):
            for i,nid in enumerate(sorted(nids)):
                pos[nid]=(i*(NODE_W+H_GAP), layer*(NODE_H+V_GAP))
    else:
        for layer,nids in sorted(layers.items()):
            for i,nid in enumerate(sorted(nids)):
                pos[nid]=(layer*(NODE_W+H_GAP), i*(NODE_H+V_GAP))
    return pos

# ---------------- Estilos nativos ----------------
def default_vertex_style(shape: str, html: bool) -> str:
    base = "whiteSpace=wrap;html=1;" if html else "whiteSpace=wrap;html=1;"
    if shape=="rhombus": return "shape=rhombus;rounded=0;"+base
    if shape=="round":   return "rounded=1;"+base
    return "rounded=0;"+base

def default_edge_style() -> str:
    return "edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;"

def style_for_vertex(shape: str, html: bool, styles: Dict[str,str], pref_keys: List[List[str]], override: Optional[str]) -> str:
    # override explícito
    s = style_from_key_or_literal(override, styles)
    if s: return ensure_html(s) if html else s
    # tentativa por chaves preferidas
    for inc in pref_keys:
        s = _find_style(styles, inc, exclude=["edge"])
        if s: return ensure_html(s) if html else s
    # fallback
    return default_vertex_style(shape, html)

def style_for_edge(styles: Dict[str,str], pref_keys: List[List[str]], override: Optional[str]) -> str:
    s = style_from_key_or_literal(override, styles)
    if s: return ensure_html(s)
    for inc in pref_keys:
        s = _find_style(styles, inc, exclude=[])
        if s: return ensure_html(s)
    return default_edge_style()

# ---------------- draw.io XML ----------------
def make_drawio_xml(spec: Dict[str,Any], positions: Dict[str,Tuple[int,int]],
                    styles: Dict[str,str],
                    mode: str,
                    override: Dict[str,Optional[str]]) -> str:
    xs=[x for x,y in positions.values()] or [0]; ys=[y for x,y in positions.values()] or [0]
    max_x=(max(xs) if xs else 0)+NODE_W+80; max_y=(max(ys) if ys else 0)+NODE_H+80
    page_w=max(1920, max_x); page_h=max(1080, max_y)
    page_id=str(uuid.uuid4())
    parts=[]
    parts.append(f'<mxfile host="auto" modified="1" agent="prompt2drawio-uml" version="24.7.1" pages="1">')
    parts.append(f'  <diagram id="{xml_attr(page_id)}" name="{xml_attr(spec.get("title") or "Page-1")}">')
    parts.append(f'    <mxGraphModel dx="2000" dy="2000" grid="1" gridSize="10" guides="1" tooltips="1" '
                 f'connect="1" arrows="1" fold="1" page="1" pageScale="1" '
                 f'pageWidth="{int(page_w)}" pageHeight="{int(page_h)}" math="0" shadow="0">')
    parts.append('      <root>')
    parts.append('        <mxCell id="0"/><mxCell id="1" parent="0"/>')

    # preferências por modo
    if mode=="er":
        v_pref = [["er.entity"], ["entity"], ["table"], ["erd"], ["sql"], ["db"]]
        e_pref = [["edge.entityrelation"], ["entityrelation"], ["er.edge"], ["relationship"]]
    elif mode=="class":
        v_pref = [["uml.class"], ["class"]]
        e_pref = [["edge.uml"], ["association"], ["edge.orthogonal"]]
    elif mode=="usecase":
        v_pref = [["usecase"], ["uml.usecase"], ["ellipse"], ["oval"]]
        e_pref = [["edge.association"], ["edge.uml"], ["edge.orthogonal"]]
    else:
        v_pref = [["shape.rect"]]
        e_pref = [["edge.orthogonal"]]

    # vertices
    for n in spec["nodes"]:
        nid=n["id"]; x,y=positions.get(nid,(0,0))
        shape=n.get("shape","rect")
        htmlFlag=bool(n.get("html",False))
        # escolhas de estilo por modo com override
        if mode=="usecase" and n["id"].startswith("A"):  # ator
            vs = style_for_vertex("round", True, styles,
                                  pref_keys=[["uml.actor"],["actor"],["stickman"],["person"]],
                                  override=override.get("actor"))
        elif mode=="usecase" and n["id"].startswith("U"):  # use case (elipse)
            vs = style_for_vertex("round", True, styles,
                                  pref_keys=[["usecase"],["ellipse"],["oval"]],
                                  override=override.get("usecase"))
        elif mode=="class":
            vs = style_for_vertex("rect", True, styles, v_pref, override.get("class"))
        elif mode=="er":
            vs = style_for_vertex("rect", True, styles, v_pref, override.get("er_entity"))
        else:
            vs = style_for_vertex(shape, htmlFlag, styles, v_pref, override.get("vertex"))

        # rótulo pode conter HTML (já escapado em nodes_edges_*); aqui SEMPRE escapamos de novo para atributo XML
        label_attr = xml_attr(n.get("label") or n.get("id", ""))
        parts.append(
            f'        <mxCell id="{xml_attr(nid)}" value="{label_attr}" style="{xml_attr(vs)}" vertex="1" parent="1">'
        )
        parts.append(f'          <mxGeometry x="{int(x)}" y="{int(y)}" width="{NODE_W}" height="{NODE_H}" as="geometry"/>')
        parts.append('        </mxCell>')

    # edges
    eid=100000
    for e in spec["edges"]:
        src=e["from"]; tgt=e["to"]; lbl=xml_attr(e.get("label",""))
        if mode=="er":
            es = style_for_edge(styles, e_pref, override.get("er_edge"))
        elif mode=="class":
            es = style_for_edge(styles, e_pref, override.get("class_edge"))
        elif mode=="usecase":
            es = style_for_edge(styles, e_pref, override.get("usecase_edge"))
        else:
            es = style_for_edge(styles, e_pref, override.get("edge"))

        parts.append(
            f'        <mxCell id="e{eid}" value="{lbl}" style="{xml_attr(es)}" edge="1" parent="1" source="{xml_attr(src)}" target="{xml_attr(tgt)}">'
        )
        parts.append('          <mxGeometry relative="1" as="geometry"/>')
        parts.append('        </mxCell>'); eid+=1

    parts.append('      </root>')
    parts.append('    </mxGraphModel>')
    parts.append('  </diagram>')
    parts.append('</mxfile>')
    return "\n".join(parts)

# ---------------- Build spec a partir do modo ----------------
def build_spec_from_mode(prompt:str, model:str, mode:str)->Dict[str,Any]:
    if mode=="er":       return nodes_edges_er(build_er(prompt, model))
    if mode=="class":    return nodes_edges_class(build_class(prompt, model))
    if mode=="sequence": return nodes_edges_sequence(build_sequence(prompt, model))
    if mode=="state":    return nodes_edges_state(build_state(prompt, model))
    if mode=="activity": return nodes_edges_activity(build_activity(prompt, model))
    if mode=="usecase":
        d=build_usecase(prompt, model)
        nodes=[]; edges=[]; name_to_id={}
        # atores (preferimos estilo/shape de ator; se não houver, round)
        for i,a in enumerate(d["actors"],1):
            nid=f"A{i}"; name_to_id[a]=nid
            nodes.append({"id":nid,"label":html_escape(a),"shape":"round","html":True})
        # casos de uso (elipse; label itálico pra diferenciar)
        for j,u in enumerate(d["usecases"],1):
            nid=f"U{j}"; name_to_id[u]=nid
            nodes.append({"id":nid,"label":"<i>"+html_escape(u)+"</i>","shape":"round","html":True})
        for r in d["relations"]:
            if r.get("from") in name_to_id and r.get("to") in name_to_id:
                edges.append({"from":name_to_id[r["from"]],"to":name_to_id[r["to"]],"label":r.get("label","") or r.get("type","association")})
        return {"title":d["title"],"direction":"LR","nodes":nodes,"edges":edges}
    # generic
    return build_generic(prompt, model)

# ---------------- Pipeline ----------------
def pipeline(prompt:str,
            model:str,
            mode:str,
            direction:Optional[str],
            add_hash:bool,
            out_base:Path,
            styles_path: Optional[Path],
            style_overrides: Dict[str,Optional[str]])->Path:

    if mode=="auto": mode=infer_mode(prompt)
    # LLM
    sp=Spinner("Gerando especificação com LLM…"); sp.start()
    try:
        spec=build_spec_from_mode(prompt, model, mode)
    finally:
        sp.stop("✔ Especificação gerada")

    # Layout
    sp=Spinner("Calculando layout…"); sp.start()
    try:
        pos=layer_layout(spec, direction)
    finally:
        sp.stop("✔ Layout calculado")

    # Estilos
    styles = load_styles(styles_path)

    # XML
    sp=Spinner("Gerando .drawio…"); sp.start()
    try:
        xml=make_drawio_xml(spec, pos, styles, mode, style_overrides)
    finally:
        sp.stop("✔ .drawio gerado")

    # hash no nome
    if add_hash:
        h=hashlib.sha1(f"{prompt}|{mode}|{time.time()}".encode("utf-8")).hexdigest()[:8]
        out_path=Path(out_base).with_suffix("")
        out_path=out_path.parent / f"{out_path.name}_{h}.drawio"
    else:
        out_path=Path(out_base).with_suffix(".drawio")

    out_path.write_text(xml, encoding="utf-8")
    return out_path

# ---------------- CLI ----------------
@click.command(context_settings=dict(help_option_names=["-h","--help"]))
@click.argument("prompt", type=str)
@click.option("--out", "out_base", type=click.Path(dir_okay=False, path_type=Path), default="diagram",
              help="Prefixo do arquivo de saída (sem extensão).")
@click.option("--model", type=click.Choice(["gpt-4o-mini","gpt-4o","gpt-4.1","gpt-4.1-mini"]) if os.getenv("OPENAI_API_KEY") else str, default=OPENAI_MODEL_DEFAULT,
              help="Modelo LLM (ex.: gpt-4o-mini ou ollama:llama3.1 / ollama:gpt-oss:20b).")
@click.option("--mode", type=click.Choice(["auto","er","class","sequence","state","activity","usecase","generic"]), default="auto",
              help="Tipo de diagrama. 'auto' detecta pelo prompt.")
@click.option("--direction", type=click.Choice(["TD","LR"]), default=None,
              help="Direção do layout (TD/LR). Se omitir, escolhe automaticamente.")
@click.option("--no-hash", is_flag=True, help="Não acrescentar hash ao nome do arquivo.")
@click.option("--styles", "styles_path", type=click.Path(dir_okay=False, path_type=Path), default=None,
              help="Caminho para styles.json colhido do draw.io (harvest).")
# Overrides finos (aceita chave do styles.json OU string literal de estilo começando com shape=)
@click.option("--style-er-entity", default=None, help="Override estilo da entidade ER (chave do styles.json ou 'shape=...').")
@click.option("--style-er-edge", default=None, help="Override estilo de aresta ER.")
@click.option("--style-class", default=None, help="Override estilo do vértice de Classe UML.")
@click.option("--style-class-edge", default=None, help="Override estilo de aresta em Classes.")
@click.option("--style-actor", default=None, help="Override estilo de Ator em Use Case.")
@click.option("--style-usecase", default=None, help="Override estilo de elipse (Use Case).")
@click.option("--style-vertex", default=None, help="Override genérico de vértice (outros modos).")
@click.option("--style-edge", default=None, help="Override genérico de aresta (outros modos).")
def main(prompt:str, out_base:Path, model:str, mode:str, direction:Optional[str], no_hash:bool,
         styles_path: Optional[Path],
         style_er_entity, style_er_edge, style_class, style_class_edge,
         style_actor, style_usecase, style_vertex, style_edge):
    """
    Exemplos:
      # DER detalhado (usando estilos nativos)
      python prompt2drawio.py "DER 2FA com usuários e autenticação separados" \
        --mode er --model ollama:gpt-oss:20b --direction LR --styles styles.json \
        --style-er-entity er.entity --style-er-edge edge.entityrelation

      # Classes UML
      python prompt2drawio.py "Diagrama de classes de usuários, AuthService e TwoFactor" \
        --mode class --styles styles.json

      # Use Case (forçando estilos específicos colhidos)
      python prompt2drawio.py "Casos de uso de Login e Reset Password com ator Usuário" \
        --mode usecase --styles styles.json \
        --style-actor "uml.actor" --style-usecase "usecase"
    """
    try:
        overrides = {
            "er_entity": style_er_entity,
            "er_edge": style_er_edge,
            "class": style_class,
            "class_edge": style_class_edge,
            "actor": style_actor,
            "usecase": style_usecase,
            "vertex": style_vertex,
            "edge": style_edge,
        }
        out=pipeline(
            prompt=prompt,
            model=model,
            mode=mode,
            direction=direction,
            add_hash=not no_hash,
            out_base=Path(out_base),
            styles_path=styles_path,
            style_overrides=overrides
        )
        print(json.dumps({"drawio": str(out.name)}, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"[ERRO] {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
