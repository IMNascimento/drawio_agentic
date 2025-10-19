# Prompt2Drawio

![Build Status](https://img.shields.io/badge/build-passing-brightgreen)
![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Version](https://img.shields.io/badge/version-2.1.0-blue)

## Introdução

**Prompt2Drawio** transforma **prompts em linguagem natural** em **diagramas nativos do draw.io (.drawio)**.  
Ele consulta um LLM (OpenAI ou Ollama local) para gerar uma **especificação JSON do diagrama**, aplica **layout por camadas** (TD/LR) e emite **XML do draw.io** diretamente — **sem Mermaid**.

Você pode **usar estilos nativos do draw.io** (cores, fontes e shapes do próprio app) coletando-os via **harvest** e aplicando-os por nome no CLI.

---

## Principais Recursos
- Geração **nativa** de `.drawio` (sem Mermaid).
- **Suporte a OpenAI e Ollama** (100% local/offline).
- **Layout automático** por camadas (TD ou LR).
- **Styles nativos** do draw.io via `styles.json` (harvest), com **auto-resolve** por chave e **override** fino por CLI.
- Saída com **hash curto** no nome para diferenciar execuções (`<out>_<hash>.drawio`).

---

## Requisitos de Sistema

- **Python 3.10+**
- (OpenAI) `OPENAI_API_KEY` no ambiente
- (Opcional) **Ollama** para rodar modelos localmente (ex.: `ollama pull llama3.1`)
- (Opcional) **draw.io Desktop** para abrir/visualizar os `.drawio` gerados

> **Não** é necessário Node/Mermaid para o pipeline atual.

---

## Instalação — Passo a Passo

### 1) Clonar e criar venv
```bash
git clone https://github.com/usuario/prompt2drawio.git
cd prompt2drawio

python -m venv venv
# Linux/macOS
source venv/bin/activate
# Windows PowerShell
# .env\Scripts\Activate.ps1

pip install -r requirements.txt
```

### 2) (Opcional) Configurar OpenAI **ou** Ollama
**OpenAI:**
```bash
export OPENAI_API_KEY="sua_chave_aqui"
# Windows PowerShell
# $env:OPENAI_API_KEY="sua_chave_aqui"
```

**Ollama:**
```bash
# instale o Ollama e baixe um modelo
ollama pull llama3.1  # ou gpt-oss:20b, etc.
```

---

## Coletando estilos nativos do draw.io (harvest)

Este projeto inclui um script para **varrer o webapp do draw.io** (pastas `drawio/src/main/webapp`) e **extrair todos os estilos** utilizados. O resultado é um `styles.json` com pares **{nome_do_estilo: string_de_estilo}**.

### 1) Garanta o webapp do draw.io descompactado
Ex.: `~/drawio_unpack_XXXXXXXX/drawio/src/main/webapp`.

### 2) Rodar o harvest
```bash
python harvest_all_styles.py "$HOME/drawio_unpack_XXXXXXXX"   --glob "**/*.xml"   --styles-out styles.json   --print-summary --debug
```
Saída típica:
```
[debug] candidato webapp: /home/user/drawio_unpack_XXXXXXXX/drawio/src/main/webapp
Arquivos considerados: 160
Células analisadas: 662 | Arquivos com algum estilo: 16 | Estilos únicos: 162

✔ Estilos extraídos: 162
- er.entity: shape=...
- edge.entityrelation: edgeStyle=entityRelationEdgeStyle;...
...
{"styles_json":"styles.json","styles_count":162}
```

> Dica: anote chaves como `er.entity`, `edge.entityrelation`, `uml.class`, `usecase`, `uml.actor`, `edge.orthogonal` etc. Elas variam por versão.

---

## Diagramas Suportados & Modos de Chamada

A CLI suporta **7 modos** por `--mode` (ou autodetecção com `--mode auto`). Abaixo, o que cada modo espera do LLM e **exemplos de chamada**.

### 1) `er` — Diagrama Entidade-Relacionamento (DER)
- Gera entidades com atributos (tipo, PK, UNIQUE, NULL) e arestas com cardinalidade.
- **Estilos típicos**: `er.entity` (vértice), `edge.entityrelation` (aresta).

**Exemplos:**
```bash
# (Ollama local, LR, com estilos nativos)
python prompt2drawio.py   "DER de autenticação 2 fatores com usuário separado da tabela de autenticação; campos, tipos e relações bem definidas"   --mode er --model ollama:gpt-oss:20b --direction LR   --styles styles.json   --style-er-entity er.entity   --style-er-edge edge.entityrelation

# (OpenAI, estilos auto-resolvidos)
python prompt2drawio.py   "DER de marketplace com Users, Products, Orders, Payments e Shipments"   --mode er --direction TD --styles styles.json
```

### 2) `class` — Diagrama de Classes UML
- Classes com atributos e métodos; relações (associação, herança etc.).
- **Estilos**: `uml.class` / `class`, `edge.uml` / `edge.orthogonal`.

**Exemplos:**
```bash
python prompt2drawio.py   "Diagrama de classes de usuários, AuthService e TwoFactor"   --mode class --styles styles.json

python prompt2drawio.py   "Domínio: Catálogo, Carrinho, Pedido, Pagamento e Entrega"   --mode class --styles styles.json   --style-class uml.class --style-class-edge edge.uml
```

### 3) `sequence` — Diagrama de Sequência UML
- Participantes/lifelines e mensagens entre eles. Direção fixada em `LR` no output.
- **Estilos**: usa o estilo padrão de vértices/arestas (pode forçar com `--style-vertex/--style-edge`).

**Exemplos:**
```bash
python prompt2drawio.py   "Sequência: Cliente -> API -> DB para login e 2FA"   --mode sequence --model ollama:llama3.1
```

### 4) `state` — Diagrama de Estados
- Estados, transições, estado inicial/final (se fornecidos).
- **Estilos**: padrão, com override opcional via `--style-vertex/--style-edge`.

**Exemplo:**
```bash
python prompt2drawio.py   "Estados de uma conta: Created -> Active -> Suspended -> Closed"   --mode state --direction TD
```

### 5) `activity` — Diagrama de Atividades
- Ações, decisões/merges, início/fim.
- **Estilos**: padrão, com override opcional via `--style-vertex/--style-edge`.

**Exemplo:**
```bash
python prompt2drawio.py   "Atividades do checkout: Start -> AddCart -> Payment -> Confirmation -> End"   --mode activity
```

### 6) `usecase` — Casos de Uso
- Atores (preferência por `uml.actor`) e elipses para casos de uso.
- **Estilos**: `uml.actor` (ator), `usecase` (elipse), `edge.association`/`edge.uml`.

**Exemplos:**
```bash
python prompt2drawio.py   "Casos de uso: Login, Reset Password; Atores: Usuário, Admin"   --mode usecase --styles styles.json   --style-actor "uml.actor" --style-usecase "usecase"

python prompt2drawio.py   "Portal: Buscar Produtos, Ver Detalhes, Adicionar ao Carrinho, Finalizar Compra; Atores: Visitante, Cliente"   --mode usecase --styles styles.json
```

### 7) `generic` — Grafo Genérico
- Nós/arestas livres com `shape` simples (`rect`, `round`, `rhombus`).
- Útil para fluxos rápidos e brainstorms.

**Exemplo:**
```bash
python prompt2drawio.py   "Pipeline de dados: Ingestão -> Limpeza -> Feature Store -> Treinamento -> Deploy"   --mode generic --direction LR
```

> Em **`--mode auto`**, o script tenta inferir o modo a partir do prompt.

---

## Exemplos Rápidos

```bash
# ER com overrides finos
python prompt2drawio.py "DER SaaS multi-tenant (Users, Tenants, Subscriptions, Invoices)"   --mode er --styles styles.json   --style-er-entity er.entity   --style-er-edge edge.entityrelation

# Classes (OpenAI)
python prompt2drawio.py "Classes do módulo de pagamentos (Gateway, Charge, Refund)"   --mode class --styles styles.json

# Usecase
python prompt2drawio.py "Casos de uso do app bancário: Transferir, Pagar Boleto, Pix"   --mode usecase --styles styles.json
```

Por padrão, o arquivo sai como `<out>_<hash>.drawio`:
```bash
python prompt2drawio.py "Meu diagrama" --out diagram
# Ex.: diagram_9af13d2c.drawio
```
Desligar hash:
```bash
python prompt2drawio.py "Meu diagrama" --out diagram --no-hash
# Ex.: diagram.drawio
```

---

## Flags da CLI

| Flag | Descrição |
|------|-----------|
| `--out` | Prefixo do arquivo de saída (default: `diagram`) |
| `--model` | `gpt-4o-mini`, `gpt-4o`, `gpt-4.1`, `gpt-4.1-mini`, ou `ollama:<modelo>` |
| `--mode` | `auto`, `er`, `class`, `sequence`, `state`, `activity`, `usecase`, `generic` |
| `--direction` | `TD` ou `LR` (auto se omitir) |
| `--no-hash` | Não acrescenta hash ao nome |
| `--styles` | Caminho p/ `styles.json` (harvest) |
| `--style-er-entity` | Override de vértice ER (chave do styles.json **ou** literal `shape=...;...`) |
| `--style-er-edge` | Override de aresta ER |
| `--style-class` | Override de vértice de Classe UML |
| `--style-class-edge` | Override de aresta de Classes |
| `--style-actor` | Override de Ator em Use Case |
| `--style-usecase` | Override de elipse em Use Case |
| `--style-vertex` | Override genérico de vértice |
| `--style-edge` | Override genérico de aresta |

---

## Solução de Problemas

**“Unescaped '<' not allowed in attributes values” ao abrir no draw.io**  
- A versão atual já faz escape de todos atributos XML. Se ainda ocorrer, verifique se houve edição manual do arquivo.

**Timeout usando `ollama:<modelo>`**  
- Verifique se o servidor Ollama está ativo em `http://localhost:11434` e o modelo foi baixado (`ollama pull ...`). O `TIMEOUT` padrão é 900s.

**Estilos não aplicados**  
- Confirme `--styles styles.json` e que as **chaves** existem (revise o resumo do harvest). Como alternativa, use um **literal de estilo** (`shape=...;...`).

**Layout ruim (aglutinado)**  
- Force `--direction LR` para grafos mais horizontais ou deixe o **auto** decidir.

---

## Estrutura do Projeto

```
prompt2drawio/
├── prompt2drawio.py          # CLI principal (gera .drawio nativo)
├── harvest_all_styles.py     # Script de harvest de estilos do draw.io
├── styles.json               # (gerado) Estilos colhidos do webapp
├── requirements.txt
└── README.md
```

---

## Contribuindo
Contribuições são bem-vindas! Envie PRs com melhorias, testes e docs.

## Licença
MIT — veja `LICENSE`.

## Autor
**Igor Muniz Nascimento** — Desenvolvedor Principal  
[GitHub: IMNascimento](https://github.com/IMNascimento)

## Agradecimentos
- diagrams.net / draw.io — base de estilos e formato `.drawio`
- OpenAI — LLM (opcional)
- Ollama — LLM local/offline
