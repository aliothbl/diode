# ROADMAP — ordem de execução do backlog avulso

> Priorização humana das SPECs future SEM programa (proposta inicial do diagnóstico de 2026-11-13; o owner reordena por PR dedicado). SPECs do programa cross-repo `diode/specs/programs/diode-stack-hardening.md` NÃO entram aqui.

> Pré-requisito de todas: a SPEC de sync do repo (programa) concluída.

1. SPEC-20261113-2141-graceful-shutdown-sigterm — onda 1 (alto/baixa)
2. SPEC-20261113-2143-stream-pel-dlq-poison — onda 1 (alto/média; agrava com 2141 ausente)
3. SPEC-20261113-2142-jwt-verificado-ingester-reconciler — onda 2 (alto/média)
4. SPEC-20261113-2147-testes-dbstore-integracao-ci — onda 2 (alto/média; rede de segurança para 2144/2151)
5. SPEC-20261113-2146-validacao-entrada-ingester — onda 3 (médio/baixa)
6. SPEC-20261113-2149-migracoes-lock-dsn-postgres — onda 3 (médio/baixa)
7. SPEC-20261113-2150-superficie-http-pprof-claims — onda 3 (médio/baixa)
8. SPEC-20261113-2152-ci-supply-chain-pinning — onda 3 (médio/baixa)
9. SPEC-20261113-2148-helm-hardening — onda 3 (médio-alto/média)
10. SPEC-20261113-2151-retencao-paginacao-ingestion-logs — onda 4 (médio/média)
11. SPEC-20261113-2153-docs-arquitetura-runbook — onda 4 (baixo/baixa)
