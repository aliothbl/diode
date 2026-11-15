# Journal — SPEC-20261114-1748-retry-branch-indisponivel

## SNAPSHOT
- **Fase:** implementação feita; falta validar no bench (`branch-sync-ingest` com netbox-branching SPEC-1741).
- **Contrato consumido** (SPEC-1741 do netbox-branching, DEC-1/DEC-2): `503` + `Retry-After` para branch transitório; `409 {"retryable": false}` para branch inutilizável; o corpo não traz nome nem status do branch.
- **Próximo passo:** `branch-sync-ingest` ×5 com os dois lados; meta 0 FAILED e nada faltando no branch.

## Decisões
- **DEC-1 — o último 503/429 chega tipado ao ops.** O cliente de apply usa `retryablehttp.PassthroughErrorHandler`: esgotados os retries (que já respeitam o `Retry-After`), a resposta volta para `BulkPlanApply`, que devolve `*netboxdiodeplugin.UnavailableError`. O cliente de leitura não muda.
- **DEC-2 — adiado = de volta a QUEUED, sem placeholder.** `RequeueApplyingIngestionLogs` (APPLYING → QUEUED, só para linhas ainda em APPLYING). Re-checagens de drift voltam para QUEUED também: `requeued_from_state` continua na linha e a re-checagem acontece no próximo claim (mesmo efeito do `ResetApplyingIngestionLogs` no startup).
- **DEC-3 — 409 só quando o corpo diz `retryable: false`.** Qualquer outro 409 continua sendo falha comum de apply.
- **DEC-4 — branch inutilizável falha fechado.** Linhas de volta a QUEUED, pausa de 30 s por worker, log ERROR e refresh do branch padrão. Nada é aplicado no main por engano.
- **DEC-5 — caminho plan-only (IngestionLogProcessor + política de auto-apply).** As linhas desse caminho estão em OPEN, não em APPLYING, então o requeue não as toca: um lote adiado fica OPEN (com o change set persistido) para revisão manual, em vez de virar falha. Registrado como limitação.
- **Métricas:** não adicionadas nesta entrega (o escopo pedia "lotes adiados" e "branch padrão terminal"); o sinal fica no log (WARN/ERROR). Pendente.

## LOG
- 2026-11-15 12:43 — SPEC ativada no `perf/bench-vs-upstream`. Implementação: `UnavailableError` + passthrough no cliente de apply; `deferBulkPlanApply` em `ops`; `RequeueApplyingIngestionLogs` no repositório (+ mock); espera do `RetryAfter` no `AutoApplyProcessor`; resultado adiado no `IngestionLogProcessor`. Testes: 3 do cliente, 3 do ops, 2 do processador; `go vet` e `go test ./...` verdes (`bench/evidence/logs/20261115-go-test-1748.log`).
