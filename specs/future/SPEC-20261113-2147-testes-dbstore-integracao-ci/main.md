# SPEC-20261113-2147: Testes do dbstore/postgres e testes de integração no CI

**Status:** draft
**Porte:** M
**Owner:** @aliothbl
**Criada:** 2026-11-13 21:47
**Ativada:** —
**Pausada em:** —
**Concluída:** —
**Commit final:** —
**Keywords:** testcontainers, postgres, sqlc, skip-locked, dedup, requeue, goose, integration_test, e2e, pytest, github-actions, coverage
**Features:** persistence, ci-supply-chain
**Branch:** _(a definir na ativação)_
**Depende de:** SPEC-20261113-2130-sync-upstream-develop-2026-11
**Programa:** —
**Impacto:** alto · **Complexidade:** média
**Estratégia upstream:** upstream-first — abrir PR em netboxlabs/diode; carregar no fork só até o merge; ao sincronizar, descartar o commit local se o upstream absorveu
**Origem:** diagnóstico 2026-11-13 (specs/DIAGNOSTICO-2026-11-13.md, achado D-18)
**Resumo:** `dbstore/postgres` tem 0% de cobertura e nenhum `_test.go`, apesar de concentrar as queries críticas de claim (`SKIP LOCKED`), dedup por hash com advisory lock e requeue. Os testes `integration_test` (Hydra via testcontainers) e o e2e Python em `tests/` não rodam no CI. Esta SPEC cria a rede de segurança que as SPECs 2143/2144/2145/2151 precisam.

## Objetivo

Toda mudança em SQL ou migration passa a ser exercitada contra um Postgres real no PR. Os testes de integração existentes deixam de ser opcionais.

## Escopo

**DENTRO:**
- `diode-server/dbstore/postgres/testutil_test.go`: helper que sobe `postgres:16-alpine` via `testcontainers-go` (a dependência já existe; ver `auth/server_hydra_integration_test.go`), roda as migrations com o `migrator` (`migrator/migrator.go`) e devolve `*Repository`. Build tag `integration_test`.
- Casos mínimos:
  - `ClaimQueuedForAutoApply` concorrente (2 goroutines, nenhum id repetido) — `queries/ingestion_logs.sql:178-191`;
  - `BulkCreateIngestionLogs` com hashes repetidos → `WasDuplicate`, `Requeued`, `requeued_from_state`;
  - `AcquireEntityHashLocks`/`BulkMarkDuplicates` (`queries/ingestion_logs.sql:62-136`);
  - trigger de contagem por estado (migration `20260528000001_fix_ingestion_log_state_counts_trigger_deadlock.sql`);
  - Up/Down de todas as migrations em sequência.
- `.github/workflows/go-test.yaml`: job novo `integration` que roda `make test-integration` (`diode-server/Makefile:59-61`) em `ubuntu-latest` (Docker disponível). Obrigatório no PR (branch protection no fork).
- Job novo `e2e` (opcional por label `run-e2e` ou nightly), com `docker compose` de `diode-server/docker/` + NetBox e `pytest tests/` (`tests/README.md`).
- `test-coverage` (`Makefile:52-57`) passa a incluir `dbstore` com a tag (relatório separado).

**FORA:**
- Testes do plugin (repo próprio).
- Cobertura de `graph/` experimental além do que já existe.

## Invariantes
- SEMPRE rodar migrations reais (as mesmas do binário) nos testes de repositório. Nunca usar um schema escrito à mão.
- NUNCA depender de serviços externos fora de containers efêmeros no CI.

## Implementação
O helper compartilha um container por pacote (`TestMain`) e isola cada teste com `TRUNCATE ... RESTART IDENTITY CASCADE`. O tempo alvo do job `integration` é ≤ 6 min.

### Modelo de dados
—

<!-- Alternativas: (a) pgx mock / sqlmock — rejeitada: não testa SKIP LOCKED, advisory locks nem triggers, que são justamente o risco; (b) Postgres como service container do GitHub Actions — aceitável, mas testcontainers mantém a paridade local/CI com o mesmo comando. -->

## Riscos
- Flakiness por tempo de boot do container. Mitigação: wait strategy por log "ready to accept connections" ×2.
- Custo de minutos de CI no fork. Mitigação: e2e só por label ou nightly.

## Sinais de sucesso
- Os PRs das SPECs de lease e PEL chegam com testes de repositório reais. A cobertura de `dbstore/postgres` sai de 0%.

## Critério de aceite
- [ ] Testes de repositório (claim concorrente, dedup, requeue, migrations up/down) | verify: `cd diode-server && go test -tags integration_test -race ./dbstore/postgres/...`
- [ ] Cobertura de `dbstore/postgres` ≥ 60% | verify: `cd diode-server && go test -tags integration_test -cover ./dbstore/postgres/ | grep -E 'coverage: ([6-9][0-9]|100)'`
- [ ] Job `integration` presente e obrigatório no CI | verify: `grep -n "test-integration" .github/workflows/go-test.yaml`
- [ ] Job e2e executado ao menos uma vez com sucesso | evidence: manual @aliothbl
