# SPEC-20261113-2148: Endurecimento do chart Helm (health, probes, PDB, imagens)

**Status:** draft
**Porte:** M
**Owner:** @aliothbl
**Criada:** 2026-11-13 21:48
**Ativada:** —
**Pausada em:** —
**Concluída:** —
**Commit final:** —
**Keywords:** helm, readiness, liveness, grpc-health, pdb, bitnamilegacy, allowInsecureImages, busybox-latest, cert-manager, digest-pinning, appVersion
**Features:** deploy-helm
**Branch:** _(a definir na ativação)_
**Depende de:** SPEC-20261113-2130-sync-upstream-develop-2026-11, SPEC-20261113-2141-graceful-shutdown-sigterm
**Programa:** —
**Impacto:** médio-alto · **Complexidade:** média
**Estratégia upstream:** upstream-first — abrir PR em netboxlabs/diode; carregar no fork só até o merge; ao sincronizar, descartar o commit local se o upstream absorveu
**Origem:** diagnóstico 2026-11-13 (specs/DIAGNOSTICO-2026-11-13.md, achado D-19)
**Resumo:** Os deployments de diode-auth, ingester e reconciler não têm readiness/liveness probes nem PDB. O chart usa `busybox:latest`, imagens `bitnamilegacy/*` congeladas com `allowInsecureImages: true` e cert-manager v1.12.0 (EOL), e o `appVersion: 1.5.0` não bate com as releases. Adicionar o serviço `grpc.health.v1` nos binários e endurecer o chart.

## Objetivo

O chart deve refletir o estado real dos pods (só recebe tráfego quem está pronto) e sobreviver a manutenção de nós. As dependências devem estar atualizadas, sem imagens flutuantes.

## Escopo

**DENTRO:**
- **Binários**: registrar `grpc_health_v1` (`google.golang.org/grpc/health`) no ingester (`ingester/component.go`, onde o `grpcServer` é criado) e no reconciler (`reconciler/server.go`, junto do `reflection.Register`, `:74`). O status muda para `SERVING` só após conectar Redis/Postgres (e JWKS, quando a SPEC-20261113-2142 existir) e volta a `NOT_SERVING` no início do `Stop()`. O diode-auth ganha `GET /healthz` (`auth/server.go`, mux existente).
- **Templates** `charts/diode/templates/diode-{auth,ingester,reconciler}-deployment.yaml`: `readinessProbe`/`livenessProbe` (gRPC nativo `grpc: {port: ...}`, Kubernetes ≥1.24; HTTP para o auth), `startupProbe` no reconciler (migrations no startup), `terminationGracePeriodSeconds` configurável (≥ o `SHUTDOWN_GRACE_SECONDS` da SPEC-20261113-2141, + 5 s).
- `templates/pdb.yaml`: PDB por componente, habilitável (`podDisruptionBudget.enabled`, default `false` com `replicaCount: 1`, já que `minAvailable: 1` com uma réplica bloqueia drain).
- Imagens:
  - `busybox:latest` (`values.yaml:6`) → tag fixa + digest;
  - imagens diode com suporte a `digest:` no helper `_helpers.tpl`;
  - plano de saída de `bitnamilegacy/*` (`values.yaml:345-441`) e de `allowInsecureImages: true` (`:39`): avaliar `bitnami/*` com assinatura, ou chart oficial `redis`/CloudNativePG. Registrar a decisão no journal na ativação.
- Subcharts (`Chart.yaml:18-45`): cert-manager v1.12.0 → linha suportada atual; ingress-nginx 4.12.1 → atual. Checar breaking changes.
- `appVersion` alinhado à tag `diode-reconciler/vX` corrente (hoje `diode-*/v2.4.0`).
- CI: `helm-lint.yaml` passa a rodar `kube-linter` ou `kubeconform` além do `ct lint`.

**FORA:**
- NetworkPolicy (SPEC-20261113-2142).
- `strategy: Recreate` do reconciler (SPEC-20261113-2144).
- Hydra em modo `--dev` no compose de desenvolvimento (não afeta o chart).

## Invariantes
- NUNCA referenciar imagem por tag flutuante (`latest`) no chart.
- SEMPRE manter os defaults compatíveis com `replicaCount: 1` (PDB desligado por default).

## Implementação
O serviço health é registrado no mesmo `grpc.Server`. A probe gRPC nativa evita sidecar `grpc_health_probe`. Para os subcharts, um PR por subchart, para isolar regressões.

### Modelo de dados
—

<!-- Alternativas: (a) probe TCP simples — rejeitada: porta aberta não significa Redis/Postgres OK; (b) manter bitnamilegacy indefinidamente — rejeitada: imagens não recebem mais patches de segurança. -->

## Riscos
- Upgrade do cert-manager envolve CRDs, e um upgrade in-place pode falhar. Mitigação: seguir o guia oficial versão a versão e testar em kind.
- Uma readiness muito estrita derruba tudo se o Redis oscilar. Mitigação: `failureThreshold` generoso e liveness só no processo (não em dependências).

## Sinais de sucesso
- `kubectl rollout status` reflete prontidão real. Durante um drain de nó, não há janela sem ingester quando `replicaCount ≥ 2`.

## Critério de aceite
- [ ] Health gRPC nos dois serviços e `/healthz` no auth, com testes | verify: `cd diode-server && go test -race -run Health ./ingester/... ./reconciler/... ./auth/...`
- [ ] Probes presentes nos 3 deployments | verify: `helm template charts/diode | grep -c "readinessProbe"`
- [ ] Nenhuma imagem `:latest` renderizada | verify: `! helm template charts/diode | grep -nE 'image: .*:latest'`
- [ ] Lint de chart e kube-linter sem erros | verify: `helm lint charts/diode`
- [ ] Instalação limpa em kind com subcharts atualizados | evidence: manual @aliothbl
