# SPEC-20261113-2151: Retenção de ingestion_logs/change_sets, paginação por keyset, hash versionado e throughput do consumidor

**Status:** draft
**Porte:** M
**Owner:** @aliothbl
**Criada:** 2026-11-13 21:51
**Ativada:** —
**Pausada em:** —
**Concluída:** —
**Commit final:** —
**Keywords:** retention, ingestion_logs, change_sets, keyset-pagination, offset, entityhash, hash-version, source_match, consumer-throughput, graph-upsert
**Features:** persistence, reconciler
**Branch:** _(a definir na ativação)_
**Depende de:** SPEC-20261113-2130-sync-upstream-develop-2026-11, SPEC-20261113-2147-testes-dbstore-integracao-ci
**Programa:** —
**Impacto:** médio · **Complexidade:** média
**Estratégia upstream:** upstream-first — abrir PR em netboxlabs/diode; carregar no fork só até o merge; ao sincronizar, descartar o commit local se o upstream absorveu
**Origem:** diagnóstico 2026-11-13 (specs/DIAGNOSTICO-2026-11-13.md, achados D-16, D-17 e D-21)
**Resumo:** As tabelas `ingestion_logs`/`change_sets` crescem sem limite: só o grafo tem `CleanupExpiredSnapshots`. A listagem pagina por OFFSET (custo O(n)), o hash de dedup muda de valor entre versões do schema sem marcador, e o consumidor do stream é um único goroutine que, com o grafo ligado, faz upsert entidade por entidade no caminho quente.

## Objetivo

Limitar o custo de armazenamento e de consulta com o tempo, tornar a troca de algoritmo de hash explícita e controlada, e remover o gargalo sequencial do consumidor.

## Escopo

**DENTRO:**
- **Retenção**: um job periódico no reconciler (componente `RetentionWorker` registrado no `server`) apaga em lotes (`DELETE ... WHERE id IN (SELECT id ... LIMIT 5000)`) os `ingestion_logs` em estado terminal (APPLIED, NO_CHANGES, IGNORED, FAILED) com `updated_at < now() - INGESTION_LOG_RETENTION_DAYS` (default 0 = desligado), junto com os `change_sets` dependentes (verificar FK/cascade nas migrations). Usar o índice `updated_at DESC` existente (`20260526000001_add_ingestion_log_updated_at_desc_index.sql`). Métrica de linhas removidas.
- **Keyset**:
  - `reconciler/logs_retriever.go:54-66` decodifica `PageToken` como offset inteiro em base64. Trocar por um token opaco `{last_id}` e uma query `WHERE id < @last_id ORDER BY id DESC LIMIT @n`.
  - Manter compatibilidade: um token numérico antigo é interpretado como offset por 1 release.
  - O mesmo para `queries/deviations.sql:14`.
- **Hash versionado**:
  - `entityhash/entityhash.go` (comentário `:29-38`: "Hash bytes are not stable across proto schema changes") ganha prefixo de versão (`v2:` + hex), com a versão do algoritmo **e** o fingerprint do descriptor do `Entity`.
  - Dedup só compara hashes da mesma versão. Mudança de versão vira um evento controlado (métrica + log), e não um replanejamento silencioso em massa.
  - Considerar o comportamento já trazido pela #601 (`source_match` no hash).
- **Throughput**:
  - o loop de `consumeIngestionStream` (`reconciler/ingestion_processor.go:195-209`) processa mensagens com um pool de `INGESTION_CONSUMER_CONCURRENCY` workers (default 1, para preservar o comportamento atual);
  - o upsert no grafo (`:430-451`, `UpsertEntity` por entidade) vira lote assíncrono com fila limitada, para não bloquear o ACK.

**FORA:**
- Particionamento de tabela por tempo (avaliar se a retenção não bastar).
- Mudança do contrato gRPC (o `PageToken` continua string opaca).

## Invariantes
- NUNCA apagar linha em estado não terminal (QUEUED, APPLYING, OPEN).
- SEMPRE preservar a ordem de ACK por mensagem: concorrência ≠ ACK fora de ordem para a mesma mensagem.
- NUNCA tratar hash de versão diferente como "não duplicado" sem registrar a métrica de troca de versão.

## Implementação
A retenção roda em lotes pequenos com `pg_sleep` curto entre eles, para não competir com o claim. A paginação muda a query sqlc e o encoder do token. O hash versionado é um prefixo na string, e a coluna `entity_hash` continua `text`.

### Modelo de dados
- Sem coluna nova obrigatória. Possível índice `(state, updated_at)` se o plano de execução da retenção pedir (já existe `add_state_time_composite_indexes`, migration `20260525000002`; confirmar com `EXPLAIN` na ativação).

<!-- Alternativas: (a) TTL via pg_cron — rejeitada: dependência de extensão nem sempre disponível; (b) particionamento nativo — adiado: migração pesada para o ganho atual; (c) hash canônico JSON (RFC 8785) estável entre versões — rejeitada: o upstream saiu dele por custo de CPU (comentário em entityhash.go). -->

## Riscos
- Retenção apaga dados que o usuário queria auditar. Mitigação: default desligado e documentação no runbook.
- O prefixo de versão do hash invalida o cache de dedup na primeira subida (1 replanejamento). Mitigação: avisar, como na #601.

## Sinais de sucesso
- O tamanho de `ingestion_logs` estabiliza em regime. O p95 de `RetrieveIngestionLogs` na página 100 fica igual ao da página 1.

## Critério de aceite
- [ ] Retenção em lote só em estados terminais | verify: `cd diode-server && go test -tags integration_test -race -run Retention ./...`
- [ ] Paginação keyset com compatibilidade de token antigo | verify: `cd diode-server && go test -race -run 'PageToken|Keyset' ./reconciler/...`
- [ ] Hash com prefixo de versão e teste de troca de versão | verify: `cd diode-server && go test -race ./entityhash/...`
- [ ] Concorrência configurável do consumidor sem regressão (`-race`) | verify: `cd diode-server && go test -race -run Consumer ./reconciler/...`
- [ ] Medição antes/depois de throughput com o grafo ligado | evidence: manual @aliothbl
