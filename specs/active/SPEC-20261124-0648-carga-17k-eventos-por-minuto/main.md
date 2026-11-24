# SPEC-20261124-0648: Sustentar 17.000 eventos/min de ponta a ponta

**Status:** active
**Porte:** G
**Owner:** @aliothbl
**Criada:** 2026-11-24 06:48
**Ativada:** 2026-11-24 06:54
**Pausada em:** —
**Concluída:** —
**Commit final:** —
**Keywords:** throughput, carga sustentada, 17k/min, 283 ent/s, backlog, dedup, entityhash, drift, janela de frescor, plugin, DRF, generate_changeset, NETBOX_WORKERS, rate-limiter, escala horizontal, réplicas, kubernetes, pgbouncer, search_path
**Features:** reconciler, observability
**Branch:** `perf/17k-por-minuto` (diode, a partir de `perf/bench-vs-upstream` @ `7708929`; só local, sem push)
**Depende de:** SPEC-20261114-0807-benchmark-performance-vs-upstream (harness `bench/`, números de referência). SPEC par no `diode-netbox-plugin` para a fase 4, a criar só se a fase 2 mostrar que ela é necessária.
**Programa:** —
**Impacto:** alto · **Complexidade:** alta
**Estratégia upstream:** upstream-first, como o resto do fork. Mas só depois do retorno da NetBox Labs ao review atual (`review/README.md`) e com novo OK do owner. A janela de frescor muda comportamento e entra como opcional, desligada por padrão.
**Origem:** pergunta do owner em 2026-11-24: "Podemos simular um cenário de 17000 eventos por minuto neste PC?" e "O que podemos fazer para conseguir realizar este cenário?"
**Resumo:** A meta é aplicar 17.000 entidades por minuto (≈283 ent/s) no NetBox de forma sustentada, sem perder nem duplicar dados. O fork aplica hoje 18,1 ent/s em create e 73,7 ent/s em reingest idêntico (cenário `large`, c=4, `review/README.md` §8.2), uma distância de 4 a 15×. O gargalo é CPU Python no NetBox (≈0,14 s de CPU por entidade criada), não o Diode. Esta SPEC mede o teto desta máquina, resolve duplicatas recentes no reconciler sem passar pelo NetBox e, se ainda faltar, reduz o custo por entidade no plugin. Por fim, valida a correção com várias réplicas do NetBox e estima as réplicas e os núcleos de que um cluster Kubernetes precisaria.

## Objetivo

Responder com medição, não com estimativa, a duas perguntas:
1. Qual o teto de ponta a ponta nesta máquina (12 núcleos, 31 GB) com o código atual?
2. Que mudanças levam esse teto a 283 ent/s sustentados na mistura de carga definida pelo owner?

O resultado esperado é o cenário de 17k/min rodando no `bench/` dentro do critério de "sustentado" do DEC-3 (journal): create e update aplicados com latência p95 ≤ 60 s (envio → todas as ingestion logs do request em estado terminal), p95 da 2ª metade da janela ≤ 1,5 × p95 da 1ª metade, backlog (QUEUED + APPLYING + stream do Redis) sem crescer nos últimos 5 min, drenagem completa e estado final correto.

## Contexto medido (ponto de partida)

| Fonte | Número |
|---|---|
| `large` c=4, create | 18,1 ent/s (≈1.090/min) |
| `large` c=4, reingest idêntico | 73,7 ± 3,2 ent/s (≈4.400/min) |
| `large` c=4, update | 15,4 ± 0,4 ent/s (≈920/min) |
| CPU do NetBox em create | ≈253% (4 workers granian) → ≈0,14 s CPU/entidade |
| Profile do plugin (py-spy) | apply/serializers DRF 56%, plan `generate_changeset` 27%, changelog 14%, espera Postgres ≈9% |
| ingester e reconciler | < 1% CPU |

Por que a reingestão idêntica custa tanto: toda duplicata cujo log anterior está em APPLIED, FAILED ou NO_CHANGES volta para QUEUED, para conferir drift no NetBox (`BulkMarkDuplicates`, `dbstore/postgres/queries/ingestion_logs.sql`, `prev_state IN (3, 4, 5)`). Ou seja, cada repetição ainda faz um plan completo no plugin.

Limites de configuração que também entram na conta: `DIODE_TO_NETBOX_RATE_LIMITER_RPS` default 20 (`reconciler/config.go:20`), `AUTO_APPLY_PROCESSOR_CONCURRENCY` default 1 (`:43`) e `AUTO_APPLY_PROCESSOR_BATCH_SIZE` default 100 (`:42`).

## Escopo

**DENTRO:**

*Fase 0: definir a carga (decisão do owner)*
- Mistura de eventos: fração de create, update e repetido. Proposta, se o owner não tiver dado real: 90% repetido, 7% update, 3% create, que é o perfil típico de discovery (Orb) reenviando o inventário.
- Duração da janela de carga: 10 min. Critério de "sustentado" (DEC-3, substitui o DEC-2): create e update aplicados com latência p95 ≤ 60 s (envio → todas as ingestion logs do request em estado terminal), p95 da 2ª metade da janela ≤ 1,5 × p95 da 1ª metade, backlog (QUEUED + APPLYING + stream do Redis) sem crescer nos últimos 5 min, drenagem completa e estado final correto. O backlog sozinho não serve: um repetido de linha ainda QUEUED só incrementa `duplicate_count`, então o backlog fica limitado ao número de entidades distintas mesmo com o pipeline atrasado.

*Fase 1: gerador de carga com taxa fixa*
- `bench/loadgen.py`: novo modo `--rate <eventos/min>` com `--mix create:update:repeat` e `--duration`. Ele envia na taxa pedida, independente da vazão do pipeline, e registra a taxa realmente enviada.
- Cenário `sustained` em `bench/run.sh`, com amostragem por segundo do backlog (linhas QUEUED/APPLYING em `ingestion_logs`, tamanho do stream no Redis), além de vazão aplicada, CPU e RSS.
- Fase de drenagem depois da janela: mede o tempo até zerar o backlog e verifica o estado final com `netbox/dump_state.py`.

*Fase 2: teto desta máquina com o código atual*
- Varredura `NETBOX_WORKERS` × `CONCURRENCY` (4/4, 8/8, 10/10, 12/12), com `DIODE_TO_NETBOX_RATE_LIMITER_RPS` e `BATCH_SIZE` ajustados para não serem o limite. Também ajuste do Postgres (`max_connections`, `shared_buffers`) no compose do bench.
- Saída: a melhor configuração e o teto em ent/s, para a mistura da fase 0 e para 100% create.

*Fase 3: janela de frescor para duplicatas (reconciler, Go)*
- Configuração nova `RECONCILER_DUPLICATE_RECHECK_WINDOW` (duração; default `0` = comportamento atual, sem mudança).
- Com janela > 0: uma duplicata cujo log anterior foi aplicado (APPLIED ou NO_CHANGES) há menos que a janela só incrementa `duplicate_count` e `last_seen`, sem voltar para QUEUED. FAILED continua sempre reenfileirando.
- O tempo de referência é o da última aplicação ou verificação do log anterior. Na ativação, verificar se `updated_at` serve ou se é preciso uma coluna própria (ex.: `last_reconciled_at`, migration goose reversível).
- Métricas: `diode_reconciler_duplicates_skipped_total` (dentro da janela) e `diode_reconciler_duplicates_requeued_total`.

*Fase 4: custo por entidade no plugin (só se a fase 3 não bastar)*
- Guiada pelo profile, uma frente por vez com A/B:
  - plan: carregar os objetos existentes do batch em lote e cachear lookups de FK que se repetem (site, role, device type);
  - apply: evitar validação repetida entre plan e apply, mantendo changelog, signals e webhooks do NetBox.
- Vira SPEC própria no `diode-netbox-plugin`. Esta SPEC só registra a decisão e o número que a justifica.

*Fase 5: escala horizontal do NetBox (várias réplicas)*
- Contexto: neste PC, N réplicas dividem os mesmos 12 núcleos e rendem o mesmo que subir `NETBOX_WORKERS`. Aqui a fase valida **correção**. A **capacidade** só se mede num cluster com mais máquinas.
- `bench/compose.replicas.yaml`: N réplicas do NetBox (3–4) atrás de um balanceador (nginx ou haproxy), Postgres e Redis únicos, com o reconciler apontando para o balanceador. Usado como `BENCH_COMPOSE_EXTRA`.
- Correção com concorrência alta (`CONCURRENCY` 16–32 entre as réplicas), nos cenários `std`, `large` e `sustained`: estado final idêntico ao de uma réplica, 0 deadlocks não tratados, 0 FAILED, 0 duplicatas.
- Mesma bateria com netbox-branching ativo: escrita em branch, sync e merge concorrentes com applies distribuídos entre réplicas (reusar `bench/branch_scenarios.py`).
- PgBouncer: medir conexões abertas por réplica. Verificar com teste se o `search_path` por conexão do netbox-branching sobrevive ao pooling por **transação**. Se não sobreviver, registrar que só o pooling por **sessão** é suportado.
- Estimativa de capacidade: núcleos de NetBox por ent/s (das fases 2 e 3) → réplicas e núcleos para 17.000/min na mistura da fase 0, com a curva de escala medida aqui até o limite dos 12 núcleos.
- Nota de implantação em Kubernetes: requisitos derivados dos testes (réplicas, requests e limits de CPU, pooling, knobs do reconciler). Implantação num cluster real fica fora.

**FORA:**
- Implantar e medir num cluster Kubernetes real (depende de infraestrutura que não existe neste PC). A fase 5 entrega a estimativa e os requisitos.
- Desligar changelog, signals ou validação do NetBox, mesmo no bench.
- Push e PR upstream (ver Estratégia upstream).

## Invariantes
- NUNCA mudar o comportamento padrão: com `RECONCILER_DUPLICATE_RECHECK_WINDOW=0`, o resultado tem que ser idêntico ao atual, inclusive na suíte de testes existente.
- NUNCA aceitar ganho de vazão com estado final errado: todo resultado da fase 2 em diante passa por `dump_state.py` e tem que bater com o estado esperado (0 duplicatas, 0 updates perdidos, 0 FAILED).
- SEMPRE ≥3 execuções por configuração, com dispersão reportada (mesma regra da SPEC-0807).
- SEMPRE medir com `bench/`, no mesmo hardware, e registrar a configuração completa de cada execução.

## Riscos
- **Drift detectado com atraso.** Com a janela ligada, uma edição manual no NetBox de um objeto que o discovery repete só é revertida ou registrada depois da janela. Mitigação: default desligado, documentado como troca explícita, janela curta sugerida (ex.: 5–15 min).
- **Gerador como gargalo.** O loadgen em Python pode não sustentar 283 ent/s sozinho. Mitigação: medir a taxa realmente enviada e, se preciso, usar vários processos.
- **Disco.** O `/` está 90% cheio (47 GB livres em 2026-11-24). Execuções longas com logs completos e `.rows.json.gz` podem encher o disco. Mitigação: limpar `bench/out/` antes e checar espaço livre no `run.sh`.
- **Concorrência alta com várias réplicas.** Até agora a correção foi validada só com c=4. Com c=16–32 podem aparecer lock, deadlock e corridas de merge e sync no branching, como as que o upstream mostrou com c=4. Mitigação: a fase 5 testa correção antes de qualquer número de capacidade.
- **PgBouncer e `search_path`.** O pooling por transação pode entregar uma conexão com o schema de outro branch, o que seria corrupção silenciosa. Mitigação: teste dedicado. Sem prova, só o pooling por sessão é suportado.
- **A fase 4 é grande.** Otimizar o caminho DRF sem quebrar a semântica do NetBox leva dias, não horas. Por isso ela depende do número da fase 3.

## Sinais de sucesso
- O cenário `sustained` a 17.000/min, na mistura da fase 0, roda 10 minutos dentro do critério do DEC-3 e termina com o estado final correto, 3 de 3 execuções.

## Critério de aceite
- [x] Mistura de carga e critério de "sustentado" definidos pelo owner (DEC no journal) | evidence: manual @aliothbl — 2026-11-24 06:54, DEC-1 e DEC-2 no journal; critério trocado pelo DEC-3 em 2026-11-24 08:50
- [ ] `loadgen.py --rate/--mix/--duration` sustenta a taxa pedida (enviada ≥ 98% da pedida) | evidence: manual @aliothbl
- [ ] Cenário `sustained` grava backlog por segundo e verifica o estado final | verify: `bench/run.sh --version candidate --scenario sustained --runs 1`
- [ ] Teto desta máquina medido na varredura da fase 2 (≥3 execuções por ponto) e registrado no journal | evidence: manual @aliothbl
- [ ] Janela de frescor com testes: default 0 sem mudança; dentro da janela não reenfileira; fora da janela e FAILED reenfileiram | verify: `cd diode-server && go test -race -run 'Duplicate|Recheck' ./reconciler/... ./dbstore/...`
- [ ] Suíte completa verde com o default | verify: `cd diode-server && go test ./...`
- [ ] 17.000/min sustentados por 10 min (critério do DEC-3: latência p95 de create/update ≤ 60 s e estável, backlog sem crescer), estado final correto, 3/3 execuções | evidence: manual @aliothbl
- [ ] N réplicas do NetBox (3–4) atrás de balanceador com c=16–32: estado final idêntico ao de uma réplica, 0 FAILED, 0 duplicatas, 3/3 execuções (com e sem netbox-branching) | evidence: manual @aliothbl
- [ ] `search_path` do netbox-branching testado com PgBouncer por transação e por sessão; modo suportado registrado como DEC | evidence: manual @aliothbl
- [ ] Estimativa de réplicas e núcleos para 17.000/min, com a curva de escala medida, e nota de requisitos para Kubernetes | evidence: manual @aliothbl
- [ ] Se a meta não for atingida sem a fase 4: SPEC par criada no `diode-netbox-plugin` com o número da fase 3 como justificativa | evidence: manual @aliothbl
