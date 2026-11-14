# SPEC-20261113-2142: Verificar assinatura do JWT no ingester e no reconciler (+ NetworkPolicy)

**Status:** draft
**Porte:** M
**Owner:** @aliothbl
**Criada:** 2026-11-13 21:42
**Ativada:** —
**Pausada em:** —
**Concluída:** —
**Commit final:** —
**Keywords:** jwt, jwks, keyfunc, unverified, interceptor, hydra, issuer, audience, networkpolicy, defense-in-depth
**Features:** auth, deploy-helm
**Branch:** _(a definir na ativação)_
**Depende de:** SPEC-20261113-2130-sync-upstream-develop-2026-11
**Programa:** —
**Impacto:** alto · **Complexidade:** média
**Estratégia upstream:** upstream-first — abrir PR em netboxlabs/diode; carregar no fork só até o merge; ao sincronizar, descartar o commit local se o upstream absorveu
**Origem:** diagnóstico 2026-11-13 (specs/DIAGNOSTICO-2026-11-13.md, achados D-2 e D-13)
**Resumo:** O ingester e o reconciler usam `NewUnverifiedJWTInterceptor`, que só faz o parse do token sem verificar a assinatura e confia que o nginx validou antes. Quem alcançar as portas gRPC por dentro do cluster entra com um token forjado. Validar via JWKS do Hydra, com issuer, audience e algoritmos fixos, e fechar a rede com NetworkPolicy.

## Objetivo

Eliminar a dependência exclusiva do ingress para autenticação. O comentário em `authutil/context.go:41-45` assume que o token "has already been verified by an upstream service" (o `auth_request` do nginx / a anotação `auth-url` do ingress). Qualquer caminho que não passe pelo ingress (port-forward, outro pod, service mesh mal configurado) aceita `{"alg":"none","scope":"diode:ingest"}`. A meta é defesa em profundidade: o serviço valida o próprio token.

## Escopo

**DENTRO:**
- Novo `authutil.NewJWKSInterceptor(logger, keyfunc, opts)` em `diode-server/authutil/`. Ele usa `jwt.NewParser(jwt.WithValidMethods([...RS256, ES256]), jwt.WithIssuer(iss), jwt.WithAudience(aud) se configurado, jwt.WithExpirationRequired())` e popula o contexto com o mesmo `ContextWithTokenScope` de hoje (`authutil/context.go`).
- Trocar `authutil.NewUnverifiedJWTInterceptor(logger)` em `cmd/ingester/main.go:119` e `cmd/reconciler/main.go:296`.
- Criação do keyfunc como no auth (`auth/server.go:147-148`: `keyfunc.NewDefaultCtx(ctx, []string{PublicServerURL + "/.well-known/jwks.json"})`). Config nova em `ingester/config.go` e `reconciler/config.go`: `OAUTH2_PUBLIC_SERVER_URL`, `OAUTH2_ISSUER`, `OAUTH2_AUDIENCE` (opcional) e `JWT_VERIFICATION_MODE=verify|unverified` (default `verify`; `unverified` só como saída de emergência, com log WARN no startup).
- Endurecer `auth/tokens.go:14-16` (`jwt.Parse(tokenString, keyfunc)` sem opções), com os mesmos `WithValidMethods`/`WithIssuer` (achado D-13).
- Chart: `templates/networkpolicy-*.yaml` (habilitável por `networkPolicy.enabled`, default `true`). Ingester e reconciler só aceitam ingress do namespace/labels do ingress-nginx e (no reconciler) do NetBox. O auth aceita do ingress e do reconciler.
- Testes: token válido, token `alg:none`, issuer errado, expirado e sem `exp`. Cada caso inválido deve retornar `codes.Unauthenticated`.

**FORA:**
- mTLS entre os serviços (avaliar depois de NetworkPolicy; requer cert-manager atualizado, ver SPEC-20261113-2148).
- Escopo por RPC no reconciler (TODO em `cmd/reconciler/main.go:298`). Vai em SPEC-20261113-2150.

## Invariantes
- NUNCA aceitar token sem assinatura válida, emitido por issuer diferente do configurado ou sem `exp`, no modo `verify`.
- SEMPRE manter o mesmo contrato de escopo no contexto (`ContextKeyScope`), para que os autorizadores existentes não mudem.

## Implementação

- O JWKS é buscado em background pelo `keyfunc` (refresh automático e rate-limit de "unknown kid"), sem custo por requisição.
- Falha ao obter o JWKS no startup → o serviço não fica pronto (integra com a readiness da SPEC-20261113-2148). Não há fallback silencioso para `unverified`.
- Hydra emite access tokens JWT só com `strategies.access_token=jwt` (já é a configuração do chart: `values.yaml` "access token strategy", ~l.481). Com `opaque`, o modo `verify` precisa de introspecção. Documentar e falhar o startup com mensagem clara.

### Modelo de dados
—

<!-- Alternativas: (a) introspecção RFC 7662 em cada chamada ao diode-auth — rejeitada: adiciona RTT por Ingest no caminho quente; JWKS valida localmente; (b) só NetworkPolicy — rejeitada: depende de CNI com suporte e de labels corretos, e não protege contra pod comprometido no mesmo namespace; (c) mTLS — adiado, não rejeitado. -->

## Riscos
- Clock skew entre o Hydra e os pods → `exp`/`nbf` rejeitados. Mitigação: `jwt.WithLeeway(30s)`.
- Instalações com `access_token` opaco quebram no upgrade. Mitigação: startup falha com erro explícito, e o modo `unverified` fica disponível para a transição.
- CNI sem suporte a NetworkPolicy: a policy é ignorada em silêncio. Mitigação: NOTES.txt avisa.

## Sinais de sucesso
- `grpcurl -H 'authorization: Bearer <alg:none>' <pod-ip>:8081 diode.v1.IngesterService/Ingest` retorna `Unauthenticated`, e o fluxo SDK → ingress → ingester continua funcionando.

## Critério de aceite
- [ ] Interceptor JWKS com testes para assinatura inválida, `alg:none`, issuer errado e expirado | verify: `cd diode-server && go test -race ./authutil/... ./auth/...`
- [ ] `cmd/ingester` e `cmd/reconciler` sem referência a `NewUnverifiedJWTInterceptor` no modo default | verify: `cd diode-server && ! grep -n "NewUnverifiedJWTInterceptor(logger)," cmd/ingester/main.go cmd/reconciler/main.go`
- [ ] `auth/tokens.go` com `WithValidMethods` e `WithIssuer` | verify: `cd diode-server && grep -n "WithValidMethods" auth/tokens.go`
- [ ] NetworkPolicies renderizam e passam lint | verify: `helm lint charts/diode && helm template charts/diode | grep -c "kind: NetworkPolicy"`
- [ ] Token forjado enviado direto ao pod do ingester é rejeitado em ambiente de teste | evidence: manual @aliothbl
