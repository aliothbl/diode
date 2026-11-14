# Feature: auth

**Keywords:** diode-auth, hydra, oauth2, client_credentials, introspect, jwks, keyfunc, jwt, scopes, diode:ingest, diode:read, netbox:read, netbox:write, token-cache, authutil
**Arquivos principais:**
  - diode-server/cmd/auth/main.go
  - diode-server/auth/server.go
  - diode-server/auth/tokens.go
  - diode-server/auth/tokencache.go
  - diode-server/authutil/context.go
  - diode-server/authutil/constants.go
  - charts/diode/templates/ingress-grpc.yaml
**Resumo:** Fachada OAuth2 sobre o Ory Hydra (token, introspect, CRUD de clients) e utilitários de escopo usados por ingester e reconciler. A verificação de token nos serviços gRPC é hoje delegada ao ingress.

## Specs desta feature
### Concluídas
—
### Planejadas (future/)
- SPEC-20261113-2142 | Verificar JWT no ingester/reconciler + NetworkPolicy | tokens não verificados fora do ingress
- SPEC-20261113-2150 | Superfície HTTP/gRPC (pprof, timeouts, reflection, mapa de escopos) | pprof no mux de métricas, servidores sem ReadHeaderTimeout

## Estado atual
O diode-auth (`auth/server.go`) obtém o JWKS do Hydra em `PublicServerURL + "/.well-known/jwks.json"` (`:147-148`, `keyfunc.NewDefaultCtx`) e valida tokens com `jwt.Parse(token, keyfunc)` (`auth/tokens.go:14-16`), sem `WithIssuer`/`WithAudience`/`WithValidMethods`. Há cache de tokens opcional (`OAUTH2_TOKEN_CACHE_*`, `auth/config.go:21-36`). O `http.Server` (`:170-173`) não tem timeouts.

Ingester e reconciler usam `authutil.NewUnverifiedJWTInterceptor` (`authutil/context.go:46`). O interceptor só lê o claim `scope` (`ParseTokenUnverified`, `:67`). O reconciler exige `diode:read` em todos os RPCs (`cmd/reconciler/main.go:297-302`), que hoje são todos de leitura. O ingester exige `diode:ingest`. O plugin NetBox valida os tokens do reconciler (`netbox:read`/`netbox:write`) por introspecção no diode-auth.

## Decisões arquiteturais ativas
- DEC-20261113-2201-auth-no-ingress [ativa, a revisar por SPEC-20261113-2142] — a fronteira de autenticação do gRPC é o ingress (`nginx.ingress.kubernetes.io/auth-url`).

## Alternativas consideradas e rejeitadas
—

## Gotchas
- SPEC-20261113-2142 | Token `alg:none` com `scope=diode:ingest` é aceito por quem alcança o pod direto (2026-11-13 21:42) — não há NetworkPolicy no chart.
- SPEC-20261113-2150 | O TODO de "escopo por RPC" em `cmd/reconciler/main.go:298` não é bug hoje (2026-11-13 21:50) — todos os RPCs são leitura. O risco é um RPC de escrita futuro herdar `diode:read`.
