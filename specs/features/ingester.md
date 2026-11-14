# Feature: ingester

**Keywords:** diode-ingester, grpc, IngesterService, Ingest, redis-streams, xadd, brotli, memory-watermark, ResourceExhausted, protoc-gen-validate
**Arquivos principais:**
  - diode-server/cmd/ingester/main.go
  - diode-server/ingester/component.go
  - diode-server/ingester/config.go
  - diode-proto/diode/v1/ingester.proto
**Resumo:** Serviço gRPC que recebe `IngestRequest` dos SDKs, valida campos mínimos e enfileira a requisição serializada (opcionalmente comprimida com Brotli) no stream Redis `diode.v1.ingest-stream`, com controle de admissão pelo uso de memória do Redis.

## Specs desta feature
### Concluídas
—
### Planejadas (future/)
- SPEC-20261113-2141 | Desligamento gracioso com SIGTERM | o processo morre sem GracefulStop em todo deploy
- SPEC-20261113-2146 | Validação completa da entrada e cardinalidade de métricas | regras PGV nunca executadas; bugs no caminho de erro

## Estado atual
`Component.Ingest` (`ingester/component.go:133`) segue esta sequência:
1. Monta atributos de métrica com campos livres do cliente (`sdk_name`, `sdk_version`, `producer_app_*`).
2. Executa `validateRequest` (`:240-268`), que só verifica presença de campos e ≥1 entidade.
3. Checa o watermark de memória do Redis (`checkRedisMemoryWatermark`, `:282-342`, que responde `ResourceExhausted`).
4. Faz `proto.Marshal`, comprime com Brotli se habilitado e roteia o stream (`streamRouter`).
5. Faz `XADD` com `{request, encoding?, ingestion_ts}`.

O `IngestResponse.errors` lista as entidades nil, mas a requisição é enfileirada mesmo assim.

A autenticação é feita pelo `NewUnverifiedJWTInterceptor` (`cmd/ingester/main.go:119`), que faz parse do JWT **sem verificar a assinatura** e depende do `auth_request` do ingress. `Stop()` (`:126-130`) faz `GracefulStop` e fecha o Redis, mas hoje nunca é chamado em SIGTERM (ver reconciler/D-1).

## Decisões arquiteturais ativas
- DEC-20261113-2201-auth-no-ingress [ativa, a revisar por SPEC-20261113-2142] — a validação de token é delegada ao ingress (nginx `auth_request` → diode-auth `/introspect`), e o serviço só lê o escopo.
- DEC-20261113-2202-admissao-por-memoria-redis [ativa] — backpressure de entrada pela razão `used_memory/maxmemory`, com fail-open em erro de INFO e OOM do XADD mapeado para `ResourceExhausted`.

## Alternativas consideradas e rejeitadas
—

## Gotchas
- SPEC-20261113-2146 | Falha de `proto.Marshal` só loga e segue (`component.go:170-174`) (2026-11-13 21:46) — enfileira payload vazio.
- SPEC-20261113-2146 | Logs de erro incluem o request inteiro (`"request", in`) (2026-11-13 21:46) — vazam dados de inventário para o log.
