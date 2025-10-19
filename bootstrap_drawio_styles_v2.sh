#!/usr/bin/env bash
set -euo pipefail

# bootstrap_drawio_styles_v2.sh
# - Extrai /opt/drawio/resources/app.asar
# - Varre TODO o diretório extraído (sem assumir 'app/resources')
# - Roda harvest_all_styles.py com --glob "**/*.xml"
# - (Opcional) chama prompt2drawio.py com --styles

OUT="${OUT:-styles.json}"
PROMPT="${PROMPT:-}"
MODE="${MODE:-er}"
DIR="${DIR:-LR}"
P2D="${P2D:-prompt2drawio.py}"

if [[ ! -f /opt/drawio/resources/app.asar ]]; then
  echo "[ERRO] /opt/drawio/resources/app.asar não encontrado."; exit 1
fi

# asar CLI local
if [[ ! -x node_modules/.bin/asar ]]; then
  echo "==> Instalando @electron/asar (local)"
  npm i -D @electron/asar >/dev/null 2>&1 || { echo "[ERRO] npm install @electron/asar falhou"; exit 1; }
fi
ASAR="npx asar"
command -v npx >/dev/null 2>&1 || ASAR="node_modules/.bin/asar"

TS=$(date +%s)
DEST="$HOME/drawio_unpack_$TS"
mkdir -p "$DEST"

echo "==> Listando (amostra) conteúdo do app.asar"
$ASAR l /opt/drawio/resources/app.asar | head -n 40 || true

echo "==> Extraindo para: $DEST"
$ASAR extract /opt/drawio/resources/app.asar "$DEST"

echo "==> Amostrando XMLs encontrados:"
find "$DEST" -type f -name "*.xml" | head -n 30 || true

# Se não houver nenhum XML, aborta com dica
if ! find "$DEST" -type f -name "*.xml" | grep -q . ; then
  echo "[ERRO] Nenhum XML encontrado dentro do asar extraído em $DEST"
  echo "       Veja o conteúdo com: tree -L 3 $DEST (ou ls -R $DEST)"
  exit 1
fi

echo "==> Rodando harvester no diretório extraído"
python3 harvest_all_styles.py "$DEST" --glob "**/*.xml" --styles-out "$OUT" --print-summary

echo "==> OK. styles.json: $OUT"

if [[ -n "$PROMPT" && -f "$P2D" ]]; then
  echo "==> Gerando diagrama com prompt2drawio.py usando $OUT"
  python3 "$P2D" "$PROMPT" --mode "$MODE" --direction "$DIR" --styles "$OUT"
fi
