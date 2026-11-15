# SPEC-20261114-1748: Reconciler trata branch indisponível como espera, não como falha

**Status:** active
**Porte:** M
**Owner:** @aliothbl
**Criada:** 2026-11-14 17:48
**Ativada:** 2026-11-15 12:43
**Pausada em:** —
**Concluída:** —
**Commit final:** —
**Keywords:** branch-padrão, default-branch, X-NetBox-Branch, 503, Retry-After, 409, netbox-branching, sync, merge, requeue, FAILED
**Features:** reconciler
**Branch:** `perf/bench-vs-upstream` (junto do harness que valida o critério de bench)
**Programa:** `specs/programs/diode-stack-hardening.md`
**Relacionadas:**
  - `aliothbl/netbox-branching:specs/future/SPEC-20261114-1741-http-branch-indisponivel` (**par obrigatório**: define os códigos consumidos aqui)
  - SPEC-20261113-2145-retry-seguro-apply-plugin (política de retry do apply; a M-1 de 2026-11-14 já restringe o retry a dial/429/503)
**Origem:** `aliothbl/netbox-branching:specs/DIAGNOSTICO-2026-11-14.md`, F6.
**Resumo:** Hoje todo `>=400` do `bulk-plan-apply` marca o lote inteiro como falha (`reconciler/ops.go:663-665`). Com o branch padrão em sync ou merge, a ingestão do intervalo vira FAILED. Com o branch padrão mergeado ou arquivado, vira FAILED para sempre. O reconciler passa a distinguir indisponibilidade transitória (esperar e reenfileirar) de terminal (parar, alertar e reler o branch padrão).

## Objetivo

Consumir o contrato da SPEC par do netbox-branching:
- `503` + `Retry-After` → o lote **não** é falha. As linhas voltam a QUEUED e o processador espera o `Retry-After` antes de reivindicar de novo.
- `409` com status terminal (merged, archived, failed) → invalidar o cache do branch padrão, reler `/default-branch/` e alertar (log ERROR + métrica). Enquanto o branch padrão continuar terminal, falhar fechado (como na #586) sem consumir o backlog.

## Escopo

**DENTRO:**
- `netboxdiodeplugin/client.go`: expor o status HTTP e o `Retry-After` no erro do `BulkPlanApply`.
- `ops.BulkPlanApply`: 503 → resultado "adiado" (linhas de volta a QUEUED, sem placeholder FAILED); 409 terminal → invalida o branch padrão.
- `AutoApplyProcessor`: respeitar o `Retry-After` antes do próximo claim.
- Métricas: lotes adiados e branch padrão terminal.
- Cenário no bench: `branch-sync-ingest` (definido na SPEC-1736 do netbox-branching) com 0 FAILED.

**FORA:**
- Os códigos em si → SPEC par.

## Invariantes
- Nenhum ingestion log vira FAILED por indisponibilidade **transitória** do branch.
- Com o branch padrão terminal, nada é aplicado no main por engano: falha fechada.

## Critério de aceite
- [x] Testes: 503 → linhas voltam a QUEUED, sem FAILED, e o próximo claim espera o `Retry-After`; 409 terminal → cache do branch padrão invalidado | verify: `cd diode-server && go test ./reconciler/... ./netboxdiodeplugin/...` — 2026-11-15 16:55, `117b107`: verde (`bench/evidence/logs/20261115-go-test-1748-connclose.log`)
- [x] Bench `branch-sync-ingest`: 0 FAILED durante um sync do branch padrão | evidence: `bench/results/20261115-162825-*` (5/5: 0 FAILED, 0 faltando, 0 timeouts) — 2026-11-15 16:55; aguarda conferência manual @aliothbl
