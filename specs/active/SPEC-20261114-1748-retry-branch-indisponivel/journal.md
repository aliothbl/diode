# Journal — SPEC-20261114-1748-retry-branch-indisponivel

## SNAPSHOT
- **Fase:** escopo completo (F6 5/5 limpo; métricas em 2026-11-16 16:14); aguardando revisão do owner.
- **Contrato consumido** (SPEC-1741 do netbox-branching, DEC-1/DEC-2): `503` + `Retry-After` para branch transitório; `409 {"retryable": false}` para branch inutilizável; o corpo não traz nome nem status do branch.
- **Próximo passo:** `branch-sync-ingest` ×5 com os dois lados; meta 0 FAILED e nada faltando no branch.

## Decisões
- **DEC-1 — o último 503/429 chega tipado ao ops.** O cliente de apply usa `retryablehttp.PassthroughErrorHandler`: esgotados os retries (que já respeitam o `Retry-After`), a resposta volta para `BulkPlanApply`, que devolve `*netboxdiodeplugin.UnavailableError`. O cliente de leitura não muda.
- **DEC-2 — adiado = de volta a QUEUED, sem placeholder.** `RequeueApplyingIngestionLogs` (APPLYING → QUEUED, só para linhas ainda em APPLYING). Re-checagens de drift voltam para QUEUED também: `requeued_from_state` continua na linha e a re-checagem acontece no próximo claim (mesmo efeito do `ResetApplyingIngestionLogs` no startup).
- **DEC-3 — 409 só quando o corpo diz `retryable: false`.** Qualquer outro 409 continua sendo falha comum de apply.
- **DEC-4 — branch inutilizável falha fechado.** Linhas de volta a QUEUED, pausa de 30 s por worker, log ERROR e refresh do branch padrão. Nada é aplicado no main por engano.
- **DEC-5 — caminho plan-only (IngestionLogProcessor + política de auto-apply).** As linhas desse caminho estão em OPEN, não em APPLYING, então o requeue não as toca: um lote adiado fica OPEN (com o change set persistido) para revisão manual, em vez de virar falha. Registrado como limitação.
- **Métricas (2026-11-16 16:14):** uma métrica cobre os dois sinais do escopo, separados pelo atributo `reason`:
  - `bulk_apply_deferred_total{reason}` conta os lotes recusados e devolvidos para QUEUED;
  - `bulk_apply_deferred_ingestion_logs_total{reason}` conta as ingestion logs desses lotes;
  - `reason` vale `unavailable` (503/429 retryable: branch em sync/merge, backpressure) ou `branch_unusable` (409: branch padrão merged/archived/failed/pending migrations, o "branch padrão terminal" do escopo).

  Alertar em `branch_unusable` > 0 pega a configuração quebrada. Uma taxa alta e contínua de `unavailable` indica um branch preso em transição. O motivo vem de `ops.BulkPlanApplyResult.DeferReason`, preenchido em `deferBulkPlanApply`. Registram a métrica o `AutoApplyProcessor` (um registro por lote, antes da pausa) e o caminho de auto-apply por política do `IngestionLogProcessor`. Os logs WARN/ERROR continuam.

## LOG
- 2026-11-16 16:14 — **Métricas** (branch `spec/20261114-1748-metricas`, a partir de `perf/bench-vs-upstream`). Testes: `auto_apply_deferred_internal_test.go` passa a exigir `RecordBulkApplyDeferred` com o motivo e o tamanho do lote (o mock falha em qualquer outra chamada de métrica); `ops_unavailable_test.go` confere `DeferReason` para 503 (`unavailable`) e 409 (`branch_unusable`). `go test ./...` no diode-server: 22 pacotes ok; `go vet` e `gofmt` limpos.
- 2026-11-15 16:55 — **Validado: F6 5/5 limpo** (`20261115-162825`, diode `65f604c`, plugin `938afa0`, netbox-branching `e3bcd51`): 0 FAILED, nada faltando no branch, 0 timeouts; por execução, 35–37 respostas 503 e 8 lotes adiados e reaplicados. Chegar aqui exigiu mais três correções, achadas uma a uma pelo bench:
  1. **Retry preso em conexão suja** (v1: 100 FAILED/execução, 4 lotes × 120 s). O 503 saía sem o servidor ler o corpo, e o retry na mesma conexão keep-alive nunca completava. Correção no netbox-branching (`e3bcd51`, drena o corpo) e aqui (`117b107`, `Connection: close` em todo apply; protege também do 503 do próprio granian, provável causa do **P-10**).
  2. **Apply do plugin não atômico no branch** (v2: 2–4 FAILED "deleted in main" em 2/5). As gravações no branch saíam em autocommit pela conexão do schema, e o objeto ficava visível antes do ChangeDiff de CREATE. Correção no diode-netbox-plugin `163d202` (atomic também na conexão do branch, por fora).
  3. **Savepoint do fallback create-or-find só no default** (v3: regressão, 75–125 FAILED com 500). Correção no plugin `938afa0`.
  - Evidências: `bench/evidence/task-outputs/20261115-{1350,1515,1610,1655}-*`, logs completos em `bench/results/*.logs/`.
- 2026-11-15 12:43 — SPEC ativada no `perf/bench-vs-upstream`. Implementação: `UnavailableError` + passthrough no cliente de apply; `deferBulkPlanApply` em `ops`; `RequeueApplyingIngestionLogs` no repositório (+ mock); espera do `RetryAfter` no `AutoApplyProcessor`; resultado adiado no `IngestionLogProcessor`. Testes: 3 do cliente, 3 do ops, 2 do processador; `go vet` e `go test ./...` verdes (`bench/evidence/logs/20261115-go-test-1748.log`).
