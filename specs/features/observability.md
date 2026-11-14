# Feature: observability

**Keywords:** opentelemetry, otlp, prometheus, metrics, traces, sentry, pprof, slog, metric-cardinality, telemetry
**Arquivos principais:**
  - diode-server/telemetry/
  - diode-server/telemetry/prometheus.go
  - diode-server/pprof/pprof.go
  - diode-server/sentry/
  - docs/observability/metrics.md
**Resumo:** Métricas OTel (exportador OTLP ou Prometheus na porta 9090), traces OTLP, Sentry para erros e pprof opcional nos três serviços.

## Specs desta feature
### Concluídas
—
### Planejadas (future/)
- SPEC-20261113-2146 | Cardinalidade de métricas no ingester | atributos livres do cliente viram séries
- SPEC-20261113-2150 | pprof fora do mux de métricas, timeouts | pprof exposto em /metrics

## Estado atual
`ServePrometheusMetrics` (`telemetry/prometheus.go:19-23`) registra `/metrics` no `http.DefaultServeMux` e sobe `http.ListenAndServe` sem timeouts. O pacote `pprof` faz `import _ "net/http/pprof"`, o que registra `/debug/pprof` no mesmo `DefaultServeMux`. Como o pacote é importado por `cmd/{auth,ingester,reconciler}`, o pprof fica acessível na porta de métricas mesmo sem `PPROF_ADDR`. Em `pprof.go:29-31` um `mux` é criado e não é usado.

Há skew de versões OTel no `go.mod`: o core está em 1.46 e os exporters de métrica em 1.36. O ingester usa `sdk_name`, `sdk_version` e `producer_app_*` como atributos sem normalizar. O catálogo de métricas está em `docs/observability/metrics.md`.

## Decisões arquiteturais ativas
- DEC-20261113-2208-otel-como-api-de-metricas [ativa] — métricas via API OTel, e o Prometheus é só um exportador alternativo.

## Alternativas consideradas e rejeitadas
—

## Gotchas
- SPEC-20261113-2150 | `import _ "net/http/pprof"` contamina o `DefaultServeMux` (2026-11-13 21:50) — nunca servir nada no DefaultServeMux.
