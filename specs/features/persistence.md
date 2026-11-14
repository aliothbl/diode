# Feature: persistence

**Keywords:** postgres, pgx, sqlc, goose, migrations, ingestion_logs, change_sets, skip-locked, advisory-lock, entity_hash, requeued_from_state, dsn, sslmode, redis
**Arquivos principais:**
  - diode-server/dbstore/postgres/repository.go
  - diode-server/dbstore/postgres/graph_repository.go
  - diode-server/dbstore/postgres/queries/ingestion_logs.sql
  - diode-server/dbstore/postgres/migrations/
  - diode-server/migrator/migrator.go
  - diode-server/sqlc.yaml
**Resumo:** Camada Postgres do reconciler: repositórios gerados por sqlc, 20 migrations goose aplicadas no startup, queries de claim com `SKIP LOCKED`, dedup por hash com advisory locks e contadores por estado via trigger.

## Specs desta feature
### Concluídas
—
### Planejadas (future/)
- SPEC-20261113-2144 | Lease nos claims de APPLYING | reset global sem dono
- SPEC-20261113-2147 | Testes do dbstore/postgres e integração no CI | 0% de cobertura no pacote
- SPEC-20261113-2149 | Lock de migrações e DSN segura | migrações concorrentes, DSN sem escape, sslmode=disable
- SPEC-20261113-2151 | Retenção, keyset e hash versionado | crescimento sem limite, paginação por OFFSET

## Estado atual
As migrations são lidas do disco em `/etc/diode/migrations` (`cmd/reconciler/main.go:283`; **não** embutidas) e aplicadas por `goose.NewProvider` sem session locker (`migrator/migrator.go:43`). A DSN é montada em formato key=value via `fmt.Sprintf`, sem escape (`main.go:79`), e `POSTGRES_SSL_MODE` tem default `disable` (`reconciler/config.go:27`).

As queries críticas ficam em `queries/ingestion_logs.sql`:
- dedup: `FindPriorIngestionLogsByEntityHashes`, `BulkMarkDuplicates`, `AcquireEntityHashLocks`;
- claim: `ClaimQueuedIngestionLogs` e `ClaimQueuedForAutoApply`, com `FOR UPDATE SKIP LOCKED`;
- `ResetApplyingIngestionLogs` global.

A listagem de logs e deviations pagina por `LIMIT/OFFSET`, com token base64 de inteiro (`reconciler/logs_retriever.go:54-66`). Só o grafo experimental tem retenção (`CleanupExpiredSnapshots`).

O pacote `dbstore/postgres` não tem nenhum `_test.go`. Os únicos testes com container real são os de integração do Hydra (`auth/*_integration_test.go`, tag `integration_test`), fora do CI.

## Decisões arquiteturais ativas
- DEC-20261113-2205-sqlc-queries-versionadas [ativa] — todo SQL fica em `queries/*.sql` e é gerado por sqlc. Não escrever SQL inline em Go.
- DEC-20261113-2206-migracoes-no-startup [ativa, a endurecer por SPEC-20261113-2149] — o reconciler migra o banco ao subir.

## Alternativas consideradas e rejeitadas
—

## Gotchas
- SPEC-20261113-2147 | Mock de banco não testa `SKIP LOCKED`/advisory locks/triggers (2026-11-13 21:47) — use testcontainers.
- SPEC-20261113-2149 | Senha com espaço ou aspas quebra a DSN (2026-11-13 21:49).
