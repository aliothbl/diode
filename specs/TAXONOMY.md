# TAXONOMY.md — vocabulário canônico de áreas

> Toda feature de `specs/features/` DEVE corresponder a uma área daqui (R.4/R.13). Área nova = confirmação explícita do usuário.

## Áreas

- upstream-sync — Sincronização do fork aliothbl/diode com netboxlabs/diode (develop, release, tags) e automação
- ingester — Serviço gRPC IngesterService.Ingest: validação, enfileiramento no Redis stream, admissão por memória
- reconciler — Consumo do stream, dedup por entityhash, processadores auto-apply/ingestion-log, orquestração plan/apply com o plugin
- auth — diode-auth (fachada OAuth2 sobre Ory Hydra) e interceptors JWT/escopo dos serviços gRPC
- persistence — Postgres (sqlc, goose, ingestion_logs/change_sets/grafo) e Redis
- deploy-helm — Chart charts/diode, docker-compose e imagens
- observability — Métricas OTel/Prometheus, traces, Sentry, pprof, logs
- ci-supply-chain — Workflows GitHub, lint, scanners, release, pinagem de dependências e actions
- docs — Documentação de arquitetura, runbooks e modelo de ameaça
