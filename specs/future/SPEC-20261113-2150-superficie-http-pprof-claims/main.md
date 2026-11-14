# SPEC-20261113-2150: Reduzir superfície HTTP/gRPC (pprof, timeouts, reflection, escopos)

**Status:** draft
**Porte:** P
**Owner:** @aliothbl
**Criada:** 2026-11-13 21:50
**Ativada:** —
**Pausada em:** —
**Concluída:** —
**Commit final:** —
**Keywords:** pprof, defaultservemux, prometheus, readheadertimeout, slowloris, grpc-reflection, scopes, gosec, g112
**Features:** observability, auth
**Branch:** _(a definir na ativação)_
**Depende de:** SPEC-20261113-2130-sync-upstream-develop-2026-11
**Programa:** —
**Impacto:** médio · **Complexidade:** baixa
**Estratégia upstream:** upstream-first — abrir PR em netboxlabs/diode; carregar no fork só até o merge; ao sincronizar, descartar o commit local se o upstream absorveu
**Origem:** diagnóstico 2026-11-13 (specs/DIAGNOSTICO-2026-11-13.md, achados D-12 e D-22)
**Resumo:** O import `_ "net/http/pprof"` registra `/debug/pprof` no `http.DefaultServeMux`, que é o mesmo mux do `/metrics` do Prometheus. Ou seja, pprof fica exposto na porta de métricas mesmo sem `PPROF_ADDR`. Os servidores de métricas e de auth não têm `ReadHeaderTimeout`. O gRPC reflection está sempre ligado no reconciler, e todos os RPCs do reconciler exigem o mesmo escopo (TODO).

## Objetivo

Expor só o que foi pedido, em cada porta, com timeouts que resistem a clientes lentos, e com autorização por RPC no reconciler.

## Escopo

**DENTRO:**
- `diode-server/pprof/pprof.go`:
  - remover o `_ "net/http/pprof"`;
  - registrar explicitamente `pprof.Index/Cmdline/Profile/Symbol/Trace` num `http.NewServeMux()` próprio;
  - remover o código morto `:29-31` (o `mux` é criado e nunca usado; o `Handler` é o `DefaultServeMux`).
  
  O pacote é importado em `cmd/{auth,ingester,reconciler}/main.go`, então hoje o efeito vale nos três binários.
- `diode-server/telemetry/prometheus.go:19-23`: mux dedicado (`mux.Handle("/metrics", promhttp.Handler())`) e `http.Server{ReadHeaderTimeout: 10s, ReadTimeout: 30s}`.
- `diode-server/auth/server.go:170-173`: `ReadHeaderTimeout`/`ReadTimeout`/`WriteTimeout`/`IdleTimeout` no `httpServer`.
- `diode-server/reconciler/server.go:74`: `reflection.Register` só com `GRPC_REFLECTION_ENABLED=true` (default `false`).
- Escopo por RPC no reconciler (TODO em `cmd/reconciler/main.go:298-299`): **reavaliado, não é defeito hoje**. Os três RPCs (`RetrieveIngestionLogs`, `RetrieveDeviations`, `RetrieveDeviationByID`, `diode-proto/diode/v1/reconciler.proto:140-148`) são leitura e exigem `diode:read` (`ScopeDiodeRead`). Escopo da SPEC: transformar o interceptor num mapa `method → scopes` com default-deny, para que um RPC de escrita futuro não herde `diode:read` por acidente. Teste: um método fora do mapa retorna `PermissionDenied`.
- `.github/golangci.yaml`: habilitar `gosec` (ao menos G112/G114) para travar regressões.

**FORA:**
- Verificação de JWT (SPEC-20261113-2142).

## Invariantes
- NUNCA servir `/debug/pprof` fora do endereço explícito `PPROF_ADDR`.
- SEMPRE definir `ReadHeaderTimeout` em todo `http.Server`.

## Implementação
Mudanças locais. Teste: subir `ServePrometheusMetrics` em porta efêmera e `GET /debug/pprof/` → 404.

### Modelo de dados
—

<!-- Alternativas: (a) manter DefaultServeMux e só documentar — rejeitada: exposição acidental de heap/goroutines; (b) remover reflection por completo — rejeitada: útil em debug, então ficou opt-in. -->

## Riscos
- O mapa default-deny pode esquecer um RPC existente. Mitigação: teste que itera `reconcilerpb.ReconcilerService_ServiceDesc.Methods` e exige entrada no mapa para cada método.

## Sinais de sucesso
- Varredura de portas no pod mostra `/debug/pprof` só em `PPROF_ADDR`. O gosec passa sem G112.

## Critério de aceite
- [ ] `/debug/pprof/` retorna 404 na porta de métricas (teste) ← specctl check
- [ ] `ReadHeaderTimeout` em todos os `http.Server` (`grep -rn "http.Server{" --include=*.go diode-server | grep -v _test` revisado) ← specctl check
- [ ] Reflection opt-in ← specctl check
- [ ] Mapa method→scopes default-deny no reconciler, com teste cobrindo todos os métodos do ServiceDesc ← specctl check
