# Journal — SPEC-20261114-0807-benchmark-performance-vs-upstream

## SNAPSHOT
- **Fase:** 4/4 — relatório entregue (`report.md`); aguardando revisão do owner. Otimização do plugin (P-3) fica como próximo ciclo.
- **Baseline:** diode `519ccec`, plugin `8c3a3bb` (worktrees em `bench/.baseline/`, criados por `bench/run.sh`).
- **Candidato:** `perf/bench-vs-upstream` (diode `e8312fe` = baseline + `specs/`; plugin `d7a0cc4` = idem). Ainda sem mudança de código.
- **Candidato atual:** M-1 (`14f4147`) + M-2 (`14ba79f`).
- **Próximo passo:** revisão do owner. Opções: (a) PRs upstream de M-1/M-2; (b) otimizar o plugin com o profile como mapa; (c) cenário `large`.

## Plano
1. **Harness** (`bench/`): stack docker idêntico; carga via SDK; binários gerados por `run.sh` com SHA256SUMS; seed do NetBox por snapshot de pgdata.
2. **Baseline**: `std` ×3 com a config padrão do upstream (RPS=20/BURST=1, batch 100, concorrência 1, timeout 5s), e ×3 com knobs "de produção" (sem gargalo artificial de rate limit) para separar gargalo de config de gargalo de código.
3. **Profiling**: pprof do reconciler/ingester (`PPROF_ADDR` já existe) e `DIODE_PROFILE=true` no plugin (contagem de queries e tempo por fase: auth/transform/match/diff/apply).
4. **Melhorias** no candidato, uma por commit, cada uma com teste + medição; depois relatório.

## Decisões de metodologia
- **DEC-1 — Hydra e nginx fora do stack.** O reconciler e o plugin falam com `bench/auth_stub.py` (token e introspect falsos). Motivo: medir o plano de dados. O custo de OAuth fica igual para os dois lados. O plugin cacheia a introspecção, então em produção esse custo também é amortizado.
- **DEC-2 — Carga direto na porta gRPC do ingester**, com protobuf montado pelas classes do SDK (o mesmo que `DiodeClient.ingest` envia). Motivo: o fluxo de token do SDK assume nginx na mesma autoridade.
- **DEC-3 — Fim de fase** = XLEN do stream = 0 e nenhuma linha em QUEUED(1)/APPLYING(8), em 2 polls seguidos. Uma entidade deduplicada reenfileira a linha existente em vez de criar outra, então contar linhas por `request_id` não funciona.
- **DEC-4 — Imagem do NetBox** construída do diretório docker do plugin candidato para os dois lados. Só o código do plugin montado muda.
- **DEC-5 — Seed**: snapshot do diretório de dados do Postgres, não `pg_dump`. O dump não restaura limpo: erro `operator does not exist: public.ltree = public.ltree` por causa do search_path vazio do pg_dump.

## Problemas encontrados (candidatos ao relatório)
- **P-1 — Timeout de 5s no cliente do plugin + retry do lote inteiro.** `NETBOX_DIODE_PLUGIN_API_TIMEOUT_SECONDS` vira `ResponseHeaderTimeout` (default 5s). `/bulk-plan-apply/` com 100 entidades pode passar disso, e o retryablehttp reenvia o lote inteiro (RetryMax=3) enquanto o primeiro ainda roda no NetBox. Verificar com medição.
- **P-2 — Rate limiter padrão 20 RPS / burst 1**, compartilhado por todos os workers. Teto teórico de 2000 ent/s com batch 100, então provavelmente não é o gargalo. Medir.
- **P-3 — Plugin aplica entidade por entidade**, serialmente, uma transação por entidade (`api/views.py:586-629`, `:152`). Lookups do matcher por entidade aninhada (N×matchers com cache frio).
- **P-4 — Ingester não verifica assinatura do JWT** (só faz parse e checa escopo; a verificação fica no nginx). Já coberto pela SPEC 2142, citar no relatório.
- **P-1 confirmado no smoke (50 entidades):** 2× `timeout awaiting response headers` em `/bulk-plan-apply/`. Efeito colateral: o NetBox termina o request que deu timeout, o retry replaneja e vê tudo aplicado, e entidades **recém-criadas** ficam registradas como `NO_CHANGES` (15/50 na fase inicial). O log de ingestão mente sobre o que aconteceu, e o trabalho no NetBox é dobrado.
- **P-6 — Platform compartilhada entre fabricantes faz pingue-pongue.** O SDK copia o `manufacturer` do Device para a `platform`. Se dois devices de fabricantes diferentes usam a mesma platform, cada apply reescreve `platform.manufacturer`, cada reingestão idêntica vira UPDATE, e o reconciler clona a linha como drift (`CloneIngestionLogForDrift`). Comportamento de dado, não de código, mas é um caso real de coletor: vai para o relatório como armadilha. O dataset do benchmark foi corrigido (platform por fabricante).
- **P-8 — Consumer group criado com `"$"` perde mensagens anteriores.** `consumeIngestionStream` faz `XGroupCreateMkStream(stream, group, "$")`. Tudo o que o ingester gravar antes de o reconciler criar o grupo pela primeira vez (deploy novo, reconciler atrasado) nunca é lido. No compose, o `depends_on` do ingester mascara o problema; no Kubernetes não há ordem garantida. Sintoma visto: `TestIngestionProcessor_DedupKeysFollowSourceMatch` fica instável sob carga de CPU (o `XAdd` do teste chega antes da goroutine criar o grupo). Fora do escopo de performance; candidato a SPEC de robustez (criar com `"0"`).
- **P-9 — Deadlock no Postgres do reconciler com auto-apply concorrente.** O ciclo `ClaimQueuedForAutoApply` × `BulkMarkDuplicates` (log do Postgres, `SQLSTATE 40P01`) ocorre com `CONCURRENCY=4`, apesar dos comentários das queries sobre ordem de locks. Observado em execuções `std` (2026-11-14 20:39–21:20): 100, 101, 1 e 2 erros no reconciler. Nos casos de ~100 erros, um lote de 100 terminou FAILED no log de ingestão. O estado do NetBox ficou correto (800 IPs), mas o registro não. Deadlock no claim é inofensivo (a transação é desfeita e o worker tenta de novo). O caminho que gera os 100 FAILED ainda não foi isolado. **Pesa sobre o novo padrão c=4 (M-3).** Candidato a SPEC: retry em 40P01 nas operações do repositório e claim por CTE com ordem de lock garantida.
- **P-5 — Consumo do stream no reconciler é serial** (XReadGroup Count=100, uma mensagem por vez, XAck + XDel em duas idas por mensagem).

## LOG
- 2026-11-14 23:12 — Correção: a entrada "23:24 — P-9 / Melhoria M-4" foi escrita às 23:10 (o horário foi digitado antes de consultar o relógio).
- 2026-11-14 23:24 — **P-9 / Melhoria M-4** (`91ecf9d`): retry em 40P01/40001 (até 5 tentativas, backoff exponencial com jitter) em `WithDedupLocks`, `BulkPersistChangeSets` (transação; o truncate pós-commit fica fora), `BulkCreateDriftDeviations` e nos dois claims. 4 testes em `dbstore/postgres/retry_internal_test.go`; `go test ./...` verde. **Validação ponta a ponta, parcial:**
  - (a) `std+nobranching`, c=4, ×5: 0 erros, mas também 0 deadlocks detectados, então o retry não foi exercitado e isso não prova nada;
  - (b) estresse c=8, batch 25, ×5: houve deadlock em 2 execuções. Uma terminou com 0 erros (retry funcionou). A outra teve **25 erros e 25 FAILED (um lote)**, com vítima fora das transações cobertas e não identificada.

  O truncate pós-commit foi descartado: na fase inicial ele não apaga nada, portanto não trava nada. `run.sh` agora grava `error_summary` (mensagens de ERROR do reconciler e statements dos deadlocks) para identificar a vítima na próxima ocorrência. **Pendente.** A causa raiz (ciclo contadores × linhas entre transações) segue; uma correção estrutural seria contadores sem hot rows (tabela de deltas append-only).
- 2026-11-14 18:20 — **Correção de timestamps deste LOG.** Várias entradas abaixo receberam horário estimado em vez do relógio, e algumas estão até no futuro em relação ao commit que as contém. Por ser append-only, as entradas não são reescritas; os horários reais (evidência: horário dos commits e início das execuções no nome dos arquivos em `bench/results/`) são:
  - "10:30 Seed por snapshot" → ~09:55 (seed às 09:20; smoke `095158`).
  - "11:40 P-1 vira bug de corretude" → ~10:15 (smoke `101157`).
  - "11:55 Melhoria M-1" → 10:28 (commit `14f4147`).
  - "13:30 Medição std" → ~11:45 (execuções `101455` e `105025`).
  - "13:45 Profiling" → ~12:10 (execução de profiling `120350`).
  - "13:50 M-2" → 13:05 (commit `14ba79f`).
  - "13:55 dump_state" → ~13:05.
  - "15:10 Bateria final" → ~14:40 (commit `ae1e79c` às 14:44).
  - "18:50 large c=1" → ~18:13 (commit `2c9895e` às 18:14).
  - "18:10 large c=4" está correto (~18:11, depois do commit `5564c69` às 18:09).
- 2026-11-14 18:50 — **Cenário large, c=1, ×1:** criação 9.4 ent/s, reingest 43.2, update 5.7; 0 timeout/deadlock/erro; **hash de estado idêntico às 2 execuções c=4** (`4539fdf406fe`). Padrão c=4 validado também em escala. Observação: o update com c=1 degrada com a escala (10.5 → 5.7 ent/s); não investigado.
- 2026-11-14 18:10 — **Cenário large, c=4, ×2:** criação 18.1 ent/s, reingest 73.7, update 15.4; 0 timeout, 0 deadlock, 0 erro no reconciler; estado idêntico. **M-3** (`5564c69`, develop): padrão `AUTO_APPLY_PROCESSOR_CONCURRENCY=4` no servidor e no sample.env. **DEC: chart Helm sem mudança** (owner, 18:15) para não divergir a versão do chart do upstream; configuração recomendada documentada no relatório §6. `run.sh` passou a registrar retries de deadlock e erros do reconciler. Execução large c=1 em andamento (prova de estado c=1 == c=4 em escala).
- 2026-11-14 15:10 — **Bateria final.** Candidato M-1+M-2 padrão: 11.34 ± 0.20 ent/s, 1800/1800 `APPLIED` na criação. Concorrência 4: upstream com ~5500 timeouts/execução, NetBox em 400% de CPU, 100% FAILED, 1518–1608 IPs e 136–189 interfaces com update perdido; fork com 20.08 ± 0.17 ent/s, 0 timeout, estado idêntico ao do fork com c=1. Upstream padrão refeito com dump completo (n=6 no total): 150–174 IPs duplicados, sem update perdido com c=1. Campo `path` (árvore por PK no 4.7) excluído do dump. Relatório em `report.md`.
- 2026-11-14 13:30 — **Medição std (1800 entidades) ×3, configuração padrão.** Baseline: initial 8.07 ± 2.77 ent/s; **IPs no fim: 1276 / 962 / 934 (esperado 800)**; execução 1 com 600 FAILED. Candidato M-1: initial 11.13 ± 0.28 (**+38%**), reingest +12%, update +17%, **800 IPs nas 3 execuções**, NetBox com ~73% de CPU contra ~100-138%.
- 2026-11-14 13:45 — **Profiling (py-spy, 4 workers, 90s, carga de criação).** 1 de 4 workers ocupado (concorrência 1 no reconciler). Dentro do request: apply 56% (serializers DRF do NetBox: `is_valid` + `create`), plan 27% (transformer 15%, matcher 10%), buffer de change log 14%, espera no Postgres só ~9%. Conclusão: CPU de Python num worker só. Alavanca principal: paralelismo entre workers.
- 2026-11-14 13:50 — **P-7 / Melhoria M-2** (`14ba79f`). O lote reivindicado vinha fora de ordem (`UPDATE ... RETURNING` via HashAggregate, conforme o plano de execução). Numa carga nova, 173/200 devices ficaram `NO_CHANGES` porque o IP com device aninhado rodava antes. Agora o lote é ordenado por id no repositório, com 2 testes.
- 2026-11-14 13:55 — dump_state renderiza GenericFK pelo objeto (antes era o `assigned_object_id` cru, dependente da ordem de criação) e grava as linhas completas (`*.rows.json.gz`). A diferença de estado entre execuções do candidato M-1 era esse artefato.
- 2026-11-14 11:40 — **P-1 vira bug de corretude.** Smoke do baseline com stack mantido: 3 timeouts de `bulk-plan-apply`, **4 IPs duplicados** no NetBox (pares criados com ~20ms de diferença, por dois workers do granian em paralelo) e 100% das entidades registradas como `NO_CHANGES`, inclusive as da fase de update. A comparação de estado acusou a divergência (21 × 20 IPs entre duas execuções do mesmo código), e o invariante de estado idêntico pegou o bug.
- 2026-11-14 11:55 — **Melhoria M-1** (`14f4147`): cliente de apply separado, timeout próprio (`NETBOX_DIODE_PLUGIN_APPLY_TIMEOUT_SECONDS`, padrão 120s) e retry só em dial error/429/503. 6 testes novos em `netboxdiodeplugin/client_apply_retry_test.go`; `go test ./...` verde.
- 2026-11-14 08:07 — SPEC criada. Branch `perf/bench-vs-upstream` local nos dois repos.
- 2026-11-14 08:30 — Primeiro boot do stack: migrações do NetBox 4.7 levam ~8 min, daí o seed.
- 2026-11-14 10:30 — Seed por snapshot do pgdata funcionando (boot do stack ~1 min). Smoke do baseline via `run.sh`: initial 4.5 ent/s, reingest 14.2, update 5.2. Linhas extras na reingestão eram clones de drift causados pelo P-6. O "todas FAILED" do primeiro smoke não se repetiu; suspeita de partida a frio (branch padrão ainda desconhecida). Vigiar.
- 2026-11-14 08:45 — Smoke manual no baseline: pipeline funciona de ponta a ponta. Bug no dataset (`10.0.0.0/16` = network ID) corrigido. Na fase inicial **todas** as 50 entidades ficaram FAILED. Causa em investigação: o erro foi sobrescrito pela reingestão.
