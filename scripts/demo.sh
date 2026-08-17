#!/usr/bin/env bash
# Fluxo completo da API, do zero à resposta com citação.
# Uso: ./scripts/demo.sh [BASE_URL]
set -euo pipefail

BASE="${1:-http://localhost:8000}"
ADMIN="${ADMIN_TOKEN:-dev-admin-token}"

command -v jq >/dev/null || { echo "instale o jq: sudo apt install jq"; exit 1; }

echo "==> 1/5  Health"
curl -sf "$BASE/health" | jq .

echo
echo "==> 2/5  Criando API key"
KEY=$(curl -sf -X POST "$BASE/v1/keys" \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: $ADMIN" \
  -d '{"name":"demo"}' | jq -r .api_key)
echo "chave: ${KEY:0:16}..."

echo
echo "==> 3/5  Ingerindo documento"
DOC=$(curl -sf -X POST "$BASE/v1/documents" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $KEY" \
  -d '{
    "title": "manual-do-colaborador.md",
    "content": "# Manual do Colaborador\n\n## Ferias\nTodo colaborador tem direito a 30 dias de ferias remuneradas por ano. O pedido deve ser feito com 45 dias de antecedencia pelo portal interno.\n\n## Trabalho remoto\nO modelo e hibrido: tres dias presenciais e dois remotos por semana. Terca e quinta sao os dias obrigatorios no escritorio.\n\n## Reembolso\nDespesas de viagem sao reembolsadas em ate quinze dias uteis apos o envio da nota fiscal. O limite diario de alimentacao e de duzentos reais.\n\n## Equipamento\nA garantia do equipamento fornecido pela empresa e de 24 meses. Defeitos devem ser reportados ao time de TI."
  }' | jq -r .document.id)
echo "documento: $DOC"

echo
echo "==> 4/5  Aguardando a ingestão terminar"
for _ in $(seq 1 30); do
  STATUS=$(curl -sf "$BASE/v1/documents/$DOC" -H "X-API-Key: $KEY" | jq -r .job.status)
  [ "$STATUS" = "completed" ] && break
  [ "$STATUS" = "failed" ] && { curl -sf "$BASE/v1/documents/$DOC" -H "X-API-Key: $KEY" | jq .; exit 1; }
  sleep 0.5
done
curl -sf "$BASE/v1/documents/$DOC" -H "X-API-Key: $KEY" | jq '{status: .job.status, chunks: .chunk_count}'

echo
echo "==> 5/5  Perguntando"
for PERGUNTA in \
  "Quantos dias de ferias eu tenho por ano?" \
  "Qual e a garantia do notebook da empresa?" \
  "Qual e a politica de bonus anual?"
do
  echo
  echo "--- $PERGUNTA"
  curl -sf -X POST "$BASE/v1/query" \
    -H "Content-Type: application/json" \
    -H "X-API-Key: $KEY" \
    -d "$(jq -nc --arg q "$PERGUNTA" '{question:$q}')" \
  | jq '{resposta: .answer, ancorada: .grounded, fontes: [.citations[] | {marcador: .marker, documento: .document_title, offset: "\(.char_start)-\(.char_end)"}], latencia_ms: .latency_ms}'
done

echo
echo "==> Consumo acumulado"
curl -sf "$BASE/v1/usage" -H "X-API-Key: $KEY" | jq .

echo
echo "Pronto. A última pergunta não tinha resposta no documento — repare que"
echo "'ancorada' voltou false em vez de o modelo inventar uma política de bônus."
