#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Harvest de estilos do draw.io (versão corrigida/final)

O que faz:
- Varre .drawio e .xml do draw.io para extrair estilos (atributos style=... de <mxCell>).
- Entende bibliotecas com <mxlibrary> (JSON embutido) e mini-graphs com <shape>.
- AUTO-DETECÇÃO de diretórios típicos quando você aponta para a raiz extraída do app.asar:
    - */drawio/src/main/webapp/shapes
    - */drawio/src/main/webapp/js/shapes
    - */app/resources/shapes
    - */resources/shapes
    - fallback: */drawio/src/main/webapp (templates) e o diretório informado
- Aplica --glob **dentro** de cada pasta que você passar.
- Deduplica estilos e cria chaves legíveis (uml.class, er.entity, edge.entityRelation, shape.ellipse...).
- Gera styles.json e imprime diagnósticos (arquivos, células, estilos únicos).
- Opções: --debug, --force-write (grava mesmo se 0 estilos), --print-summary.
"""

import os
import re
import json
import html
import glob
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import click

# --------- regex/const ---------
STYLE_KV_RE = re.compile(r'([a-zA-Z0-9_.]+)=([^;]*);?')
MXCELL_RE = re.compile(r'<mxCell([^>]*)>', re.IGNORECASE | re.DOTALL)
ATTR_STYLE_RE = re.compile(r'style="([^"]*)"', re.IGNORECASE)
ATTR_EDGE_RE = re.compile(r'edge="1"', re.IGNORECASE)
MXLIB_RE = re.compile(r'<mxlibrary>(.*?)</mxlibrary>', re.IGNORECASE | re.DOTALL)

# Candidatos de diretórios de "shapes" dentro do app.asar extraído:
SHAPES_DIR_CANDIDATES = [
    "drawio/src/main/webapp/shapes",
    "drawio/src/main/webapp/js/shapes",
    "app/resources/shapes",
    "resources/shapes",
]

# --------- utils de estilo ---------
def normalize_style(style: str) -> str:
    """Normaliza a ordem das chaves pra facilitar deduplicação."""
    kv = STYLE_KV_RE.findall(style or "")
    kv = [(k.strip(), v.strip()) for k, v in kv if k.strip()]
    kv.sort(key=lambda x: x[0].lower())
    return ';'.join([f"{k}={v}" for k, v in kv]) + (';' if kv else '')

def guess_key_from_style(style: str, is_edge: bool) -> str:
    """
    Heurística de nome:
      - shape=mxgraph.U.V -> "U.V"
      - shape=ellipse -> "shape.ellipse"
      - edgeStyle=entityRelationEdgeStyle -> "edge.entityRelation"
      - fallback: shape.rect / edge.orthogonal
    """
    d = dict(STYLE_KV_RE.findall(style or ""))
    if not is_edge:
        shape = d.get('shape', '')
        if shape.startswith('mxgraph.'):
            return shape.split('mxgraph.', 1)[1]
        if shape:
            return f"shape.{shape}"
        return "shape.rect"
    else:
        est = d.get('edgeStyle', '')
        if est:
            key = est.replace('EdgeStyle','')
            return f"edge.{key}"
        arr = d.get('endArrow') or d.get('startArrow')
        if arr:
            return f"edge.{arr}"
        return "edge.orthogonal"

def safe_key(key: str) -> str:
    key = key.strip().lower()
    key = re.sub(r'[^a-z0-9_.\-]+', '-', key)
    key = re.sub(r'-+', '-', key).strip('-')
    return key or "style"

def add_unique(mapping: Dict[str, str], base_key: str, style: str) -> str:
    """Insere no dict usando chave única (sufixa -1, -2 se colidir) e deduplica por conteúdo normalizado."""
    k = safe_key(base_key)
    s_norm = normalize_style(style)
    for ek, es in mapping.items():
        if normalize_style(es) == s_norm:
            return ek
    if k not in mapping:
        mapping[k] = style
        return k
    i = 1
    while f"{k}-{i}" in mapping:
        if normalize_style(mapping[f"{k}-{i}"]) == s_norm:
            return f"{k}-{i}"
        i += 1
    mapping[f"{k}-{i}"] = style
    return f"{k}-{i}"

# --------- parsers ---------
def extract_cells_from_drawio_xml(xml: str) -> List[Tuple[str, bool]]:
    """
    Retorna lista de (style, is_edge).
    - is_edge=True se a célula tem edge="1".
    """
    out: List[Tuple[str, bool]] = []
    for m in MXCELL_RE.finditer(xml):
        attrs = m.group(1)
        style_m = ATTR_STYLE_RE.search(attrs)
        edge_m = ATTR_EDGE_RE.search(attrs)
        if style_m:
            style = html.unescape(style_m.group(1)).strip()
            if style:
                out.append((style, bool(edge_m)))
    return out

def extract_from_mxlibrary(xml: str) -> List[Tuple[str, bool]]:
    """
    Em bibliotecas: <mxlibrary> contém JSON com uma lista de objetos; cada item tem 'xml' com um mini-graph.
    """
    out: List[Tuple[str, bool]] = []
    lib_m = MXLIB_RE.search(xml)
    if not lib_m:
        return out
    txt = html.unescape(lib_m.group(1)).strip()
    try:
        arr = json.loads(txt)
    except Exception:
        return out
    for item in arr if isinstance(arr, list) else []:
        xml_item = item.get('xml')
        if xml_item and isinstance(xml_item, str):
            out.extend(extract_cells_from_drawio_xml(xml_item))
    return out

def harvest_from_file(path: Path) -> List[Tuple[str, bool]]:
    try:
        xml = path.read_text(encoding='utf-8', errors='ignore')
    except Exception:
        # como fallback, tenta binário + decodificação bruta
        try:
            xml = path.read_bytes().decode('utf-8', errors='ignore')
        except Exception:
            return []
    cells = extract_cells_from_drawio_xml(xml)
    cells += extract_from_mxlibrary(xml)
    return cells

# --------- coleta de arquivos ---------
def expand_inputs_with_candidates(inputs: List[Path], debug: bool) -> List[Path]:
    """
    Se um caminho parece ser a raiz extraída do app.asar (ex.: ~/drawio_unpack_123456),
    adiciona candidatos de subpastas de shapes.
    """
    expanded: List[Path] = list(inputs)
    for base in list(inputs):
        if base.is_dir():
            for rel in SHAPES_DIR_CANDIDATES:
                cand = (base / rel).resolve()
                if cand.exists() and cand.is_dir():
                    expanded.append(cand)
                    if debug:
                        click.echo(f"[debug] candidato shapes: {cand}")
            # também tenta webapp se existir (templates/ pode ter estilos úteis)
            webapp = (base / "drawio/src/main/webapp").resolve()
            if webapp.exists() and webapp.is_dir():
                expanded.append(webapp)
                if debug:
                    click.echo(f"[debug] candidato webapp: {webapp}")
    # remove duplicatas mantendo ordem
    seen = set()
    uniq: List[Path] = []
    for p in expanded:
        rp = str(p.resolve())
        if rp not in seen:
            uniq.append(p)
            seen.add(rp)
    return uniq

def build_file_list(paths: List[Path], glob_pat: Optional[str], debug: bool) -> List[Path]:
    files: List[Path] = []
    for p in paths:
        if p.is_dir():
            if glob_pat:
                pattern = str(p / glob_pat)
                if debug: click.echo(f"[debug] glob dentro de {p}: {pattern}")
                for g in glob.glob(pattern, recursive=True):
                    gp = Path(g)
                    if gp.suffix.lower() in (".drawio", ".xml"):
                        files.append(gp)
            else:
                files.extend(list(p.rglob("*.drawio")))
                files.extend(list(p.rglob("*.xml")))
        else:
            if p.suffix.lower() in (".drawio", ".xml"):
                files.append(p)
    # dedup
    files = [Path(x) for x in dict.fromkeys(str(f.resolve()) for f in files)]
    if debug:
        click.echo(f"[debug] total de arquivos candidatos: {len(files)}")
    return files

# --------- CLI ---------
@click.command(context_settings=dict(help_option_names=["-h", "--help"]))
@click.argument("inputs", nargs=-1, type=click.Path(exists=True, path_type=Path))
@click.option("--glob", "glob_pat", type=str, default=None,
              help="Padrão de glob APLICADO DENTRO de cada pasta (ex.: '**/*.xml').")
@click.option("--styles-out", type=click.Path(dir_okay=False, path_type=Path), default=Path("styles.json"),
              help="Arquivo de saída do styles.json.")
@click.option("--print-summary", is_flag=True, help="Imprime resumo dos estilos coletados (top 20).")
@click.option("--debug", is_flag=True, help="Logs detalhados de descoberta de caminhos/arquivos.")
@click.option("--force-write", is_flag=True, help="Grava styles.json mesmo se nenhum estilo for extraído.")
def main(inputs: List[Path], glob_pat: Optional[str], styles_out: Path,
         print_summary: bool, debug: bool, force_write: bool):
    """
    Exemplos:

    # AppImage/.deb: passe a pasta EXTRAÍDA do app.asar
    python harvest_all_styles.py ~/drawio_unpack_123456 --glob "**/*.xml" --styles-out styles.json --print-summary

    # Direto no diretório 'shapes' (se você já souber o caminho)
    python harvest_all_styles.py ~/drawio_unpack_123456/drawio/src/main/webapp/shapes --glob "**/*.xml" --styles-out styles.json

    # Colher de um .drawio seu (com muitos shapes na página)
    python harvest_all_styles.py ./meu_diagrama.drawio --styles-out styles.json --print-summary
    """
    if not inputs:
        click.echo("[ERRO] Passe ao menos um arquivo/pasta.", err=True)
        sys.exit(1)


    in_paths = list(inputs)
    in_paths = expand_inputs_with_candidates(in_paths, debug=debug)

    files = build_file_list(in_paths, glob_pat, debug=debug)

    if not files:
        click.echo("[ERRO] Nenhum .drawio/.xml encontrado nesses caminhos.", err=True)
        sys.exit(1)

    click.echo(f"Arquivos considerados: {len(files)}")

    styles: Dict[str, str] = {}
    count_cells = 0
    files_with_any = 0

    for f in files:
        try:
            cells = harvest_from_file(f)
            if cells:
                files_with_any += 1
            count_cells += len(cells)
            for style, is_edge in cells:
                base_key = guess_key_from_style(style, is_edge)
                add_unique(styles, base_key, style)
        except Exception as e:
            if debug:
                click.echo(f"[debug] Falha ao ler {f}: {e}", err=True)

    click.echo(f"Células analisadas: {count_cells}  |  Arquivos com algum estilo: {files_with_any}  |  Estilos únicos: {len(styles)}")

   
    if not styles and not force_write:
        click.echo("[ERRO] Nenhum estilo foi extraído. Dicas:", err=True)
        click.echo("  - Aponte para diretórios de 'shapes' reais (verifique */shapes dentro do app.asar).", err=True)
        click.echo("  - Como alternativa, crie um .drawio com vários shapes nativos e passe esse arquivo aqui.", err=True)
        click.echo("  - Ou use --force-write para gravar um styles.json vazio.", err=True)
        sys.exit(1)

    try:
        styles_out.write_text(json.dumps(styles, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        click.echo(f"[ERRO] Falha ao escrever {styles_out}: {e}", err=True)
        sys.exit(1)

    if print_summary:
        click.echo(f"\n✔ Estilos extraídos: {len(styles)}")
        # top 20
        i = 0
        for k, v in styles.items():
            snip = (v[:120] + "...") if len(v) > 120 else v
            click.echo(f"- {k}: {snip}")
            i += 1
            if i >= 20:
                break

    
    print(json.dumps({"styles_json": str(styles_out), "styles_count": len(styles)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
