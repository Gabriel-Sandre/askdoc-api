# AskDoc API

API de perguntas e respostas sobre documentos privados (RAG), com **busca híbrida**, **citações verificáveis** e **rastreio de custo por requisição**.

Você envia seus documentos, faz perguntas em linguagem natural e recebe a resposta **com o offset exato de onde cada afirmação veio** — não um "segundo o documento X" genérico.

```bash
git clone https://github.com/Gabriel-Sandre/askdoc-api.git && cd askdoc-api
docker compose up --build
./scripts/demo.sh
```

Sobe API + Postgres com pgvector e roda o fluxo inteiro. **Sem precisar de nenhuma chave de API** — o provider offline embutido faz o sistema funcionar de ponta a ponta com custo zero.

Documentação interativa em <http://localhost:8000/docs>.

---

## O problema

Um assistente que responde sobre documentos internos tem dois modos de falhar, e os dois são caros:

1. **Inventar.** O modelo responde com confiança algo que não está em lugar nenhum. Sem citação rastreável, ninguém percebe.
2. **Não achar.** A busca puramente vetorial erra justamente onde precisão importa mais: códigos de produto, números de artigo, siglas, nomes próprios.

Esta API ataca os dois de frente.

## Como ataca

### 1. Busca híbrida com fusão RRF

Embeddings entendem paráfrase e sinônimo, mas confundem `Art. 483` com `Art. 384` — no espaço vetorial eles são quase o mesmo ponto, e para quem perguntou são coisas completamente diferentes. Busca lexical acerta esses casos e erra nos sinônimos.

A solução é rodar as duas e fundir os rankings com **Reciprocal Rank Fusion**: `score = Σ 1/(k + posição)`.

RRF combina **posições**, não **scores** — então não é preciso normalizar escalas incomparáveis (distância de cosseno vs. `ts_rank` do Postgres), que é onde a maioria das implementações caseiras se perde.

Uma propriedade não óbvia da curva `1/(k+rank)`, e que está fixada em teste: ela é convexa, então **1º + 3º lugar vence 2º + 2º**. Uma média simples de posições daria empate. Não é detalhe cosmético — é o que faz o RRF premiar quem uma das estratégias considera excelente sem descartar o consenso.

Toda resposta expõe `vector_rank` e `keyword_rank` de cada trecho recuperado, então dá para auditar qual estratégia trouxe o quê.

### 2. Citação com offset

Cada chunk guarda `char_start` / `char_end` do documento original. A resposta devolve apenas as fontes que o modelo **realmente citou**, com o intervalo exato — o suficiente para um front-end destacar o trecho no documento.

Se o modelo inventa uma fonte `[9]` quando só existiam 5 trechos, a citação é descartada em silêncio. Se a resposta não cita nada, `grounded` volta `false`. O cliente sempre sabe se pode confiar.

A invariante `texto_original[char_start:char_end] == chunk.text` é testada diretamente, inclusive depois de passar pelo banco. Se ela quebrar, a API continua respondendo — só passa a citar o trecho errado, que é pior do que falhar.

### 3. Rastreio de custo

Cada consulta grava tokens de entrada, tokens de saída, custo estimado e latência. `GET /v1/usage` agrega por chave. Sem isso, a primeira fatura inesperada da OpenAI vira uma investigação forense.

---

## Arquitetura

```
                    ┌──────────────────────────────────────────┐
   POST /v1/query   │  FastAPI                                 │
  ────────────────► │  ├─ middleware: request_id + latência    │
                    │  ├─ auth: API key (SHA-256)              │
                    │  └─ rate limit: janela deslizante        │
                    └────────────────┬─────────────────────────┘
                                     │
                    ┌────────────────▼─────────────────────────┐
                    │  core/rag.py  — orquestração             │
                    └──┬──────────────────────────────┬────────┘
                       │                              │
          ┌────────────▼───────────┐     ┌────────────▼─────────────┐
          │  core/retrieval.py     │     │  providers/              │
          │                        │     │  ├─ fake     (offline)   │
          │  vetorial ─┐           │     │  ├─ openai               │
          │            ├─► RRF     │     │  └─ anthropic            │
          │  lexical ──┘           │     └──────────────────────────┘
          └────────────┬───────────┘
                       │
          ┌────────────▼────────────────────────────────────────────┐
          │  Postgres                                               │
          │  ├─ pgvector + índice HNSW  (similaridade de cosseno)    │
          │  └─ tsvector + índice GIN   (full-text search)           │
          └─────────────────────────────────────────────────────────┘
```

Ingestão é assíncrona: o upload responde **202** na hora com um `job_id`, e o cliente acompanha por polling.

```
POST /v1/documents ──► 202 + job_id ──► [background] extrai → chunk → embed → grava
                                                          │
GET /v1/documents/{id} ◄──────── status: pending → running → completed | failed
```

### Estrutura

```
app/
├── main.py            middleware, lifespan, tratamento global de erro
├── config.py          settings tipadas via pydantic-settings
├── models.py          SQLAlchemy 2.0 (declarativo tipado)
├── schemas.py         contrato público (Pydantic) — separado do ORM de propósito
├── security.py        API keys com hash, comparação constant-time
├── rate_limit.py      janela deslizante, isolada para virar Redis sem tocar nas rotas
├── api/               rotas HTTP, uma por domínio
├── core/
│   ├── chunking.py    divisão com offsets preservados
│   ├── retrieval.py   busca híbrida + RRF
│   ├── rag.py         contexto, prompt, citações
│   └── ingest.py      pipeline de ingestão
└── providers/         abstração de LLM/embeddings (fake, OpenAI, Anthropic)
```

---

## Decisões de projeto

**Por que um provider offline?** Não é um brinquedo para "fazer os testes passarem". Ele resolve dois problemas reais: a suíte roda inteira no CI **sem nenhum segredo** e de forma reprodutível, e qualquer pessoa clona o repositório e vê o sistema funcionando sem gastar um centavo. O embedding usa *hashing trick* (bag-of-words normalizado em L2) — não captura sinônimo como um modelo treinado, mas captura sobreposição de termos, o bastante para o retrieval se comportar de forma coerente e para os testes afirmarem algo de verdade.

**Por que embedding e chat são interfaces separadas?** Porque a Anthropic não expõe endpoint de embeddings. Um único `AIProvider` quebraria na primeira combinação real (Claude para gerar + OpenAI para embeddar). As duas interfaces permitem misturar.

**Por que HTTP puro em vez dos SDKs?** Uma dependência a menos, contrato explícito, e dá para ver exatamente o que vai na requisição. Retry com backoff exponencial cobre 429 e 5xx, que são as falhas que realmente acontecem.

**Por que `api_key_id` desnormalizado em `chunks`?** Toda busca filtra por dono. Manter a coluna lá evita um JOIN em `documents` no caminho quente do retrieval.

**Por que 404 em vez de 403 para documento de outra chave?** 403 confirmaria que o documento existe. 404 não entrega nada.

**Por que dois health checks?** `/health` (liveness) não pode depender do banco — senão uma queda momentânea do Postgres faz o orquestrador matar pods saudáveis e piorar o incidente. `/health/ready` (readiness) checa o banco e tira a réplica do balanceador.

**Por que janela deslizante no rate limit?** Janela fixa permite o dobro do limite na virada: 60 requisições às 11:59:59 e mais 60 às 12:00:00.

**Por que o mesmo código roda em SQLite e Postgres?** Testes em SQLite são rápidos e sem setup. Produção usa Postgres com pgvector e full-text search nativo. A única diferença real está no tipo da coluna de embedding e na consulta de similaridade — e o CI roda a suíte inteira **nos dois**, porque senão o SQLite esconderia qualquer erro de SQL específico do Postgres.

---

## Limitações conhecidas

Coisas que eu faria diferente com mais tempo, e o motivo de não estarem aqui:

- **Rate limit em memória.** Com N réplicas, o limite efetivo vira N × limite. A troca para Redis mexe só na classe `SlidingWindowLimiter`.
- **`BackgroundTasks` em vez de fila.** A task vive no processo da API e não sobrevive a um deploy no meio do caminho. A fronteira já está desenhada para a troca: `run_ingestion` recebe só o `document_id` e abre a própria sessão, exatamente como um worker Celery/arq faria.
- **Sem migrations.** O schema é criado no startup. Um projeto em produção precisa de Alembic desde o primeiro dia.
- **Sem reranker.** Um cross-encoder depois do RRF melhoraria bastante a precisão do top-3, ao custo de latência.
- **Full-text em config `simple`.** Trocar para `portuguese` liga stemming e stopwords em PT; é mudar em dois lugares (`db.py` e `retrieval.py`).

---

## Endpoints

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/health` | Liveness — não toca no banco |
| `GET` | `/health/ready` | Readiness — verifica o banco |
| `POST` | `/v1/keys` | Cria API key *(admin)* |
| `GET` | `/v1/keys` | Lista chaves *(admin)* |
| `DELETE` | `/v1/keys/{id}` | Revoga chave *(admin)* |
| `POST` | `/v1/documents` | Ingere texto puro → **202** |
| `POST` | `/v1/documents/upload` | Upload `.txt` `.md` `.csv` `.json` `.pdf` → **202** |
| `GET` | `/v1/documents` | Lista paginada |
| `GET` | `/v1/documents/{id}` | Detalhe + status do job |
| `DELETE` | `/v1/documents/{id}` | Remove documento e chunks |
| `POST` | `/v1/query` | Pergunta → resposta com citações |
| `GET` | `/v1/usage` | Consumo agregado da chave |

Autenticação: header `X-API-Key` em todas as rotas `/v1`, exceto `/v1/keys` (que usa `X-Admin-Token`).

### Exemplo de resposta

```jsonc
{
  "answer": "Todo colaborador tem direito a 30 dias de férias remuneradas por ano [1].",
  "grounded": true,
  "citations": [
    {
      "marker": 1,
      "document_title": "manual-do-colaborador.md",
      "char_start": 34,
      "char_end": 152,          // ← offset exato para destacar no documento
      "score": 0.0325,
      "excerpt": "Todo colaborador tem direito a 30 dias de ferias..."
    }
  ],
  "usage": { "prompt_tokens": 412, "completion_tokens": 28, "cost_usd": 0.0000786 },
  "latency_ms": 143
}
```

---

## Rodando localmente sem Docker

```bash
pip install -e ".[dev]"
cp .env.example .env          # o default já é SQLite + provider offline
make dev                      # http://localhost:8000/docs
```

## Usando modelos reais

No `.env`:

```bash
LLM_PROVIDER=openai           # ou anthropic
EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=sk-...
EMBEDDING_DIM=1536            # text-embedding-3-small
```

> Ao trocar a dimensão do embedding, reingerir os documentos — vetores de dimensões diferentes não são comparáveis.

## Testes

```bash
make test     # suíte completa
make lint     # ruff
```

Cobrem, entre outros:

- a invariante de offset do chunking, inclusive atravessando o banco;
- a fusão RRF, incluindo a propriedade de convexidade que uma "simplificação" quebraria;
- citação de marcador alucinado sendo descartada;
- isolamento entre chaves (documento, consulta, uso e rate limit);
- chave revogada perdendo acesso, e a chave em claro nunca sendo persistida;
- ciclo completo de ingestão, incluindo duplicata (409) e tipo não suportado (415).

O CI roda tudo em Python 3.11 e 3.12, depois **de novo** contra Postgres com pgvector, e ainda faz o build da imagem Docker.
