# Feature: reconciler

**Keywords:** diode-reconciler, IngestionProcessor, AutoApplyProcessor, IngestionLogProcessor, xreadgroup, consumer-group, backpressure, bulk-plan, bulk-plan-apply, default-branch, branching, dedup, entityhash, change-set, deviation
**Arquivos principais:**
  - diode-server/cmd/reconciler/main.go
  - diode-server/reconciler/ingestion_processor.go
  - diode-server/reconciler/auto_apply_processor.go
  - diode-server/reconciler/ingestion_log_processor.go
  - diode-server/reconciler/ops.go
  - diode-server/reconciler/backpressure.go
  - diode-server/netboxdiodeplugin/client.go
  - diode-server/entityhash/entityhash.go
**Resumo:** Consome o stream de ingestão, persiste `ingestion_logs` com deduplicação por hash de entidade e orquestra o plan/apply contra o `diode-netbox-plugin` (auto-apply ou revisão manual), respeitando o branch padrão do NetBox.

## Specs desta feature
### Concluídas
—
### Planejadas (future/)
- SPEC-20261113-2130 | Sync upstream (#600 tags_resolved, #601 source_match) | atraso de 3 commits
- SPEC-20261113-2141 | Desligamento gracioso com SIGTERM | SIGTERM ignorado
- SPEC-20261113-2143 | PEL, DLQ e mensagens venenosas | PEL nunca recuperado, panic em mensagem malformada, perda em Postgres fora
- SPEC-20261113-2144 | Lease nos claims de APPLYING | reset global causa apply duplicado em rolling update
- SPEC-20261113-2145 | Retry seguro no apply ao plugin | POST não idempotente reenviado; FAILED definitivo em erro transitório
- SPEC-20261113-2151 | Retenção, keyset, hash versionado, throughput | crescimento sem limite e consumidor sequencial

## Estado atual
**Consumo**: `IngestionProcessor` faz `XREADGROUP` no grupo `diode-reconciler`, com consumidor `diode-reconciler-<hostname>` (`ingestion_processor.go:140`), lendo só `">"` (`:172-177`), sem `XAUTOCLAIM`. Cada mensagem passa por decode (Brotli + proto), cálculo de `entity_hash` e `BulkCreateIngestionLogs` (dedup com advisory locks), e depois recebe **ACK+XDEL incondicional** (`:270-285`). Erro de decode retorna antes do ACK e derruba o loop (`:207`). Type assertions sem `ok` em `:224`/`:250`.

**Processadores de banco** (escolha por `AUTO_APPLY_CHANGESETS`, `cmd/reconciler/main.go:227-244`):
- `AutoApplyProcessor`: claim QUEUED→APPLYING (`SKIP LOCKED`, lote 100), depois `POST /bulk-plan-apply/` (`ops.go:647`).
- `IngestionLogProcessor`: `POST /bulk-plan/` → OPEN, para revisão.

No startup, `ResetApplyingIngestionLogs` devolve **todas** as linhas APPLYING para QUEUED (`main.go:227-230`). Os dois processadores pausam quando `XLEN > INGESTION_LOG_PROCESSOR_BACKPRESSURE_THRESHOLD` (100).

**Cliente do plugin**: retryablehttp com 3 tentativas, espera de 150 ms a 2 s e `ResponseHeaderTimeout` de 5 s (`netboxdiodeplugin/client.go:47,190-214`), com a política padrão (repete POST). Rate limit de 20 rps. O branch padrão vem de `/default-branch/` a cada 5 min (`ops.go:26`). `ErrDefaultBranchNotFound` grava nil (`ops.go:147-153`), e desde a #586 o apply falha fechado com o cache frio.

**Desligamento**: `server/server.go:118` registra `os.Interrupt, os.Kill`. SIGTERM não é tratado.

## Decisões arquiteturais ativas
- DEC-20261113-2200-ack-regardless-backpressure [ativa] (upstream #591) — toda entrada do stream recebe ACK+XDEL, mesmo com falha, para que entradas falhas não fiquem contando em XLEN e travem o gate de backpressure. Qualquer mudança (SPEC-20261113-2143) precisa preservar essa garantia.
- DEC-20261113-2203-fail-closed-branch-desconhecido [ativa] (upstream #586) — sem branch carregado, o AutoApply não aplica.
- DEC-20261113-2204-dedup-por-hash-sem-metadata [ativa, ajustada pela #601] — o hash é SHA-256 do proto determinístico sem `metadata`, exceto `source_match` após a #601. Não é estável entre versões do schema.

## Alternativas consideradas e rejeitadas
—

## Gotchas
- SPEC-20261113-2143 | Consumidor nomeado por hostname (2026-11-13 21:43) — cada pod novo cria um consumidor novo, e o PEL do antigo fica órfão para sempre.
- SPEC-20261113-2144 | `ResetApplyingIngestionLogs` em todo startup (2026-11-13 21:44) — com dois pods vivos (rollout), reverte trabalho em voo.
- SPEC-20261113-2145 | Comentário `DefaultBranchFetchTimeout` diz "matches the bulk-plan-apply client timeout" (30 s), mas o timeout de resposta do cliente é 5 s (`client.go:47`) (2026-11-13 21:45) — não confiar no comentário.
