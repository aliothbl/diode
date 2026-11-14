# SPEC-20261113-2152: CI e cadeia de suprimentos do fork (pinning, linters, workflows de release)

**Status:** draft
**Porte:** P
**Owner:** @aliothbl
**Criada:** 2026-11-13 21:52
**Ativada:** —
**Pausada em:** —
**Concluída:** —
**Commit final:** —
**Keywords:** github-actions, sha-pinning, dependabot, reusable-workflow, semantic-release, dockerhub, golangci, gosec, errorlint, docker-compose-latest, fork-ci
**Features:** ci-supply-chain
**Branch:** _(a definir na ativação)_
**Depende de:** SPEC-20261113-2130-sync-upstream-develop-2026-11
**Programa:** —
**Impacto:** médio · **Complexidade:** baixa
**Estratégia upstream:** misto — pinning, linters e as tags `latest` do compose vão como PR upstream; a **desativação de release no fork** é customização local, isolada em arquivo próprio para não conflitar no sync
**Origem:** diagnóstico 2026-11-13 (specs/DIAGNOSTICO-2026-11-13.md, achado D-20)
**Resumo:** As actions oficiais (`checkout@v4`, `setup-go@v5`, `upload-artifact@v4`, `checkout@v3` no buf) não estão pinadas por SHA. O `server-release.yaml` chama workflows reutilizáveis de **`netboxlabs/diode/...@develop`** (código de outro repo, em branch móvel). O Makefile instala ferramentas `@latest` e o compose usa `nginx:latest`/`redis-stack-server:latest`. No fork, os workflows de release (DockerHub, gh-pages do chart) não devem disparar.

## Objetivo

1. Builds reprodutíveis e resistentes a tag hijack.
2. O fork nunca publica artefatos (imagens no DockerHub, chart no gh-pages, releases semânticas) nem executa código de workflow de outro repositório em branch móvel.

## Escopo

**DENTRO:**
- Pinning por SHA (comentário `# vX.Y.Z`) em todo `uses:` de `.github/workflows/*.yaml`. Hoje não pinados: `labeler.yaml:12-13`, `go-test.yaml:31,33`, `reusable_semantic_release_get_next_version.yaml:37-38,125,144`, `reusable_semantic_release.yaml:30-31`, `protobuf-break-detection.yaml:15` (`checkout@v3`), `golangci-lint.yaml:20,22`, `helm-release.yaml:25`, `ruff-lint.yaml:30,32`, `server-release.yaml:63,70`.
- `server-release.yaml:40,124`: `netboxlabs/diode/.github/workflows/...@develop` → caminho local `./.github/workflows/...` (upstream PR), ou SHA.
- `.github/dependabot.yml` para `github-actions`, `gomod` (`/diode-server`) e `docker`.
- `.github/golangci.yaml`: além de `bodyclose`/`revive`, habilitar `gosec`, `errorlint`, `contextcheck`, `noctx` (corrigir ou `nolint` justificado).
- `diode-server/Makefile:29-33`: ferramentas `@latest` → versões fixas.
- `diode-server/docker/docker-compose.yaml`: `nginx:latest`, `redis-stack-server:latest` → tags fixas.
- **Guard de fork** (customização local): em `helm-release.yaml`, `server-release.yaml`, `reusable_semantic_release*.yaml` e `container-rescan.yaml`, adicionar `if: github.repository == 'netboxlabs/diode'` nos jobs que publicam. A condição só desliga no fork e mantém o upstream intacto, então dá para propor upstream como boa prática e evitar o conflito local.
- Workflow `fork-ci.yaml` próprio do fork (arquivo novo, sem conflito) com os gates relevantes em push para `develop`: go-test, golangci e helm-lint.

**FORA:**
- Automação de sync (SPEC-20261113-2140).
- Assinatura de imagens (cosign) e SBOM: o fork não publica imagens.

## Invariantes
- NUNCA executar workflow reutilizável de outro repositório referenciado por branch.
- NUNCA publicar artefato a partir do fork.

## Implementação
Usar `pinact` ou `ratchet` para gerar os SHAs. O guard `if: github.repository == ...` é uma linha por job. Validar com `actionlint` e `zizmor`.

### Modelo de dados
—

<!-- Alternativas: (a) desabilitar workflows pela UI do GitHub no fork — complementar, mas não versionado; (b) apagar os workflows de release no fork — rejeitada: gera conflito a cada sync upstream. -->

## Riscos
- O Dependabot gera volume de PRs no fork. Mitigação: agrupar (`groups`) e cadência semanal.
- Os linters novos geram muitos achados. Mitigação: ativar com baseline (`new-from-rev`).

## Sinais de sucesso
- O `zizmor` não reporta `unpinned-uses` nem execução de workflow remoto por branch. Pushes em `release` no fork não disparam publicação.

## Critério de aceite
- [ ] Todo `uses:` pinado por SHA (`grep -nE 'uses: [^ ]+@v[0-9]' .github/workflows/*.yaml` vazio) ← specctl check
- [ ] Nenhuma referência `@develop` a workflow remoto ← specctl check
- [ ] Jobs de publicação com guard de repositório ← specctl check
- [ ] Dependabot configurado e golangci com gosec/errorlint ← specctl check
