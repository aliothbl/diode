# Feature: deploy-helm

**Keywords:** helm, chart, charts/diode, hydra, bitnami, bitnamilegacy, ingress-nginx, cert-manager, probes, pdb, networkpolicy, strategy, securityContext, docker-compose
**Arquivos principais:**
  - charts/diode/Chart.yaml
  - charts/diode/values.yaml
  - charts/diode/templates/
  - diode-server/docker/docker-compose.yaml
  - .github/workflows/helm-lint.yaml
  - .github/workflows/helm-release.yaml
**Resumo:** Chart Helm `diode` (v1.15.6) que instala auth, ingester e reconciler, com subcharts de Redis, Postgres, Hydra, ingress-nginx e cert-manager. Inclui o docker-compose de desenvolvimento.

## Specs desta feature
### Concluídas
—
### Planejadas (future/)
- SPEC-20261113-2142 | NetworkPolicy para ingester/reconciler/auth | sem isolamento de rede
- SPEC-20261113-2144 | `strategy: Recreate` no reconciler (interino) | rolling update causa apply duplicado
- SPEC-20261113-2148 | Health, probes, PDB e imagens | sem probes, imagens congeladas ou flutuantes, subcharts EOL

## Estado atual
O chart tem deployments, configmaps, services e serviceaccounts para os 3 serviços, um job de bootstrap de clients do Hydra, ingress gRPC (com `auth-url` para o diode-auth) e ingress HTTP. A #594 (já no fork) adicionou securityContext e resource requests. Não há readiness/liveness/startup probes, PDB nem NetworkPolicy. A estratégia de deploy é a padrão (RollingUpdate), e `replicaCount: 1` vale para todos.

Dependências (`Chart.yaml`): bitnami common 2.31.1, redis 20.11.5, postgresql 16.6.3, hydra 0.61.0, ingress-nginx 4.12.1, cert-manager v1.12.0. As imagens Bitnami vêm de `bitnamilegacy/*`, com `allowInsecureImages: true` (`values.yaml:39`). Os init containers usam `busybox:latest` (`values.yaml:6`). O `appVersion` é `1.5.0`, enquanto as tags dos serviços estão em `v2.4.0`.

## Decisões arquiteturais ativas
- DEC-20261113-2207-bitnamilegacy-temporario [ativa, a revisar por SPEC-20261113-2148] (upstream) — uso de `bitnamilegacy` após a mudança de distribuição da Bitnami (bitnami/charts#30850).

## Alternativas consideradas e rejeitadas
—

## Gotchas
- SPEC-20261113-2148 | PDB `minAvailable: 1` com `replicaCount: 1` bloqueia o drain de nó (2026-11-13 21:48) — o default precisa ser desligado.
- SPEC-20261113-2148 | Upgrade do cert-manager mexe em CRDs (2026-11-13 21:48) — seguir o guia versão a versão.
