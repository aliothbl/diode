# Feature: ci-supply-chain

**Keywords:** github-actions, go-test, golangci-lint, codeql, trivy, helm-lint, buf-breaking, ruff, semantic-release, dockerhub, dependabot, sha-pinning, reusable-workflow
**Arquivos principais:**
  - .github/workflows/
  - .github/golangci.yaml
  - diode-server/Makefile
  - diode-server/docker/docker-compose.yaml
**Resumo:** Pipelines de CI e release herdados do upstream: testes Go com `-race` e cobertura, lint, CodeQL, Trivy, lint e release do chart, detecção de breaking change no proto e release semântico com push de imagens no DockerHub.

## Specs desta feature
### Concluídas
—
### Planejadas (future/)
- SPEC-20261113-2147 | Testes de integração e e2e no CI | integração e e2e nunca rodam
- SPEC-20261113-2152 | Pinning, linters e guard de release no fork | actions sem SHA, workflow remoto @develop, release disparável no fork
- SPEC-20261113-2140 | Automação recorrente do sync upstream | —

## Estado atual
O `go-test.yaml` roda `make test-coverage` (`-race -cover`, filtrando gen/cmd/mocks/sentry) em PR e comenta a cobertura. O `make test-integration` (tag `integration_test`) não está em nenhum workflow. O golangci (`.github/golangci.yaml`) usa os linters padrão mais `bodyclose` e `revive`.

`server-release.yaml` e `helm-release.yaml` disparam em push para `release`. O `server-release.yaml` usa `secrets.DOCKERHUB_*` e chama `netboxlabs/diode/.github/workflows/reusable_semantic_release*.yaml@develop`. Várias actions oficiais não estão pinadas por SHA (`checkout@v4`, `setup-go@v5`, `checkout@v3` no buf). As de terceiros (peter-evans, docker/login) estão pinadas. O Makefile instala ferramentas `@latest`, e o compose usa `nginx:latest`/`redis-stack-server:latest`.

## Decisões arquiteturais ativas
- DEC-20261113-2209-fork-nao-publica [ativa] (SPEC-20261113-2152) — o fork nunca publica imagens, charts ou releases.

## Alternativas consideradas e rejeitadas
—

## Gotchas
- SPEC-20261113-2152 | Apagar workflows de release no fork gera conflito a cada sync (2026-11-13 21:52) — use o guard `if: github.repository == 'netboxlabs/diode'`.
