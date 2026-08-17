# Roteiro de portfólio — estágio backend/full-stack

Este documento é o plano em volta do código. O projeto entregue (AskDoc API) é o carro-chefe; abaixo estão os próximos, em ordem de impacto por hora investida.

---

## Como recrutador e tech lead realmente olham

Vale entender contra o que você está competindo. A maioria dos portfólios de estágio tem: um CRUD de tarefas, um clone de rede social e um projeto de curso. Todos sem teste, sem README e com um único commit chamado "projeto final".

Quem avalia gasta **menos de dois minutos** por repositório, e nessa ordem:

1. **O README.** Se em 30 segundos não dá para entender o que é, por que existe e como rodar, o resto não é lido.
2. **Roda?** Se `docker compose up` funciona de primeira, você já está à frente da maioria.
3. **Tem teste?** A presença de uma pasta `tests/` e um CI verde muda a percepção mais do que qualquer feature.
4. **Os commits.** Histórico com mensagens descritivas mostra que você trabalhou de forma incremental, não que copiou tudo de uma vez.
5. **Só então, o código.**

O AskDoc API foi construído para passar nesses cinco filtros. Os próximos seguem a mesma régua.

**Três projetos bem-acabados batem oito pela metade.** Um repositório abandonado no seu perfil trabalha contra você.

---

## Projeto 2 — API de reservas à prova de overbooking

**Por que este.** É o melhor complemento possível ao projeto de IA, porque ataca a dúvida que todo entrevistador tem sobre estagiário: *"essa pessoa entende banco de dados de verdade, ou só chama o ORM?"*

Concorrência é o assunto que quase nenhum candidato de estágio consegue discutir, e é o que separa quem aprendeu a programar de quem aprendeu a construir sistemas. Se você conseguir explicar em entrevista por que duas requisições simultâneas vendem o mesmo assento e como você provou que o seu código não vende, isso vale mais que dez CRUDs.

**O que é.** API de reserva de assentos (evento, cinema, voo — tanto faz) com estoque limitado.

**O núcleo — e é isso que importa:**

- Reserva sob concorrência sem vender o mesmo assento duas vezes. Implemente das duas formas: **lock pessimista** (`SELECT ... FOR UPDATE`) e **lock otimista** (coluna de versão + retry). Meça as duas.
- **Chave de idempotência** (`Idempotency-Key`): o cliente reenvia a requisição após timeout e não gera reserva duplicada. É como Stripe e todo gateway sério funcionam.
- **Reserva com expiração**: assento fica travado por 10 minutos e volta ao estoque se não for pago.
- **Máquina de estados explícita**: `disponível → reservado → pago → cancelado`, com transições inválidas rejeitadas.

**A prova — o diferencial real.** Um teste que dispara 200 requisições simultâneas para 10 assentos e afirma que **exatamente 10** tiveram sucesso e 190 receberam 409. Coloque o resultado no README, com número. É a diferença entre "implementei controle de concorrência" e "provei que funciona".

Depois rode um teste de carga (k6 ou Locust) comparando pessimista vs. otimista e ponha o gráfico no README. Agora você tem dado, não opinião.

**Stack.** Mesma do AskDoc (FastAPI + Postgres + Docker) — reuso de setup, foco no que é novo. Se quiser mostrar amplitude, este é um bom candidato para Java/Spring Boot ou .NET, já que você mencionou conhecer.

**Escopo.** Um fim de semana. Sem tela, sem pagamento real, sem autenticação elaborada. Concorrência bem-feita e testada vale mais que dez telas.

**O que dizer na entrevista:** *"Comecei com lock otimista, mas sob 200 requisições concorrentes a taxa de retry passou de 60% e a latência p99 explodiu. Troquei para `SELECT FOR UPDATE` no assento específico — não na tabela — e resolvi. O teste de concorrência está no repositório."* Essa frase sozinha muda uma entrevista.

---

## Projeto 3 — Front-end para o AskDoc

**Por que este.** O AskDoc é uma API. Recrutador de RH não roda `curl`. Este projeto transforma o carro-chefe em algo que se demonstra em 30 segundos, num link — e ainda cobre a parte "full-stack" da vaga.

**O que é.** Uma página em Next.js: você arrasta um PDF, faz uma pergunta, e a resposta aparece **com o trecho citado destacado no documento ao lado**.

Aquele `char_start`/`char_end` que a API devolve existe exatamente para isso. Ver o highlight pular para o parágrafo certo é o tipo de coisa que faz quem está assistindo entender o valor na hora — e nenhum outro portfólio de estágio vai ter isso.

**Escopo mínimo que já impressiona:**

- Upload com barra de progresso e polling do status do job.
- Campo de pergunta com resposta em streaming (ou pelo menos um loading honesto).
- Visualizador de documento com o intervalo citado destacado, clicável a partir da citação.
- Deploy na Vercel, com a API em Railway/Render/Fly.io. **Link funcionando no topo do README.**

**Escopo.** Dois a três dias. Não invente design system: use shadcn/ui ou Tailwind puro. O destaque da citação é a única coisa que precisa ficar realmente boa.

---

## Projeto 4 — Uma contribuição open source de verdade

**Por que este.** É o único item da lista que não é você avaliando seu próprio trabalho. Um PR mergeado num projeto real prova que você lê código dos outros, segue convenção alheia e sobrevive a code review — exatamente o que você vai fazer no estágio.

**Como fazer sem travar:**

1. Escolha uma biblioteca Python que você **já usou** (as do AskDoc servem: FastAPI, SQLAlchemy, httpx, pydantic).
2. Filtre por label `good first issue` ou `documentation`.
3. Comece por documentação ou por um teste faltando. Não tente refatorar o core na primeira tentativa.
4. Leia o `CONTRIBUTING.md` inteiro antes de abrir qualquer coisa.

Um PR pequeno e mergeado vale mais que cinco PRs ambiciosos e ignorados. Não precisa ser heroico — precisa ser real.

**Escopo.** Algumas horas espalhadas ao longo de semanas. Faça em paralelo com o resto.

---

## Ordem sugerida

| Quando | O quê | Por quê |
|---|---|---|
| Este fim de semana | Subir o AskDoc no GitHub, revisar o README, garantir CI verde | Já está pronto — falta publicar |
| Fim de semana seguinte | Projeto 2 (concorrência) | Maior salto de percepção técnica |
| Depois | Projeto 3 (front) | Torna o carro-chefe demonstrável |
| Em paralelo, sem pressa | Projeto 4 (open source) | Sinal externo, único que não é auto-avaliação |

---

## Antes de publicar o AskDoc

- [ ] Trocar `<url-do-repo>` no README pela URL real
- [ ] Rodar `make test` e `make lint` — CI verde no primeiro push
- [ ] Rodar `./scripts/demo.sh` e **gravar um GIF de 20 segundos** para o topo do README (use `asciinema` ou `peek`). GIF é o que faz alguém parar de rolar a página.
- [ ] Descrição e tópicos do repositório preenchidos: `rag`, `fastapi`, `postgresql`, `pgvector`, `llm`
- [ ] Fixar (*pin*) o repositório no perfil do GitHub
- [ ] Foto e bio no perfil, com link para os projetos fixados
- [ ] Commits em incrementos com mensagens descritivas — se você subir tudo num commit só, perde o sinal do item 4 lá do começo

## Como falar dos projetos

Não descreva funcionalidade — descreva **decisão e trade-off**. Compare:

> ❌ "Fiz uma API de RAG com FastAPI e Postgres que responde perguntas sobre documentos."

> ✅ "Fiz uma API de RAG. A parte interessante foi o retrieval: busca vetorial pura errava em código de produto e número de artigo, porque no espaço de embeddings `Art. 483` e `Art. 384` são quase o mesmo ponto. Adicionei busca lexical e fundi os dois rankings com RRF, que combina posições em vez de scores — assim não preciso normalizar escalas incomparáveis. E toda resposta devolve o offset exato da fonte, então dá para verificar se o modelo alucinou."

A segunda resposta mostra que você **entendeu um problema**, não que seguiu um tutorial. Prepare uma dessas para cada projeto.

E saiba responder *"o que você faria diferente?"*. A seção **Limitações conhecidas** do README do AskDoc existe para isso: admitir o rate limit em memória e a ausência de fila real, sabendo exatamente o que trocaria, demonstra mais maturidade do que fingir que está tudo perfeito.
