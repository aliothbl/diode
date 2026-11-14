# SPEC-20261113-2140: Automação da sincronização dos 3 forks com netboxlabs

**Status:** draft
**Porte:** M
**Owner:** @aliothbl
**Criada:** 2026-11-13 21:40
**Ativada:** —
**Pausada em:** —
**Concluída:** —
**Commit final:** —
**Keywords:** upstream, sync, fork, merge-upstream, github-actions, schedule, workflow-token, release-guard
**Features:** upstream-sync, ci-supply-chain
**Branch:** _(a definir na ativação)_
**Depende de:** SPEC-20261113-2130 (diode), diode-sdk-python SPEC-20261113-2131, diode-netbox-plugin SPEC-20261113-2132. As três sincronizações manuais precisam estar concluídas.
**Programa:** `specs/programs/diode-stack-hardening.md`
**Impacto:** alto · **Complexidade:** baixa
**Estratégia upstream:** fork-only. Arquivos novos com prefixo `fork-`, sem tocar em nenhum arquivo upstream.
**Origem:** diagnóstico 2026-11-13 (`specs/DIAGNOSTICO-2026-11-13.md`, §5.4).
**Resumo:** Workflow agendado em cada fork que detecta atraso para `upstream/{develop,release}`. Ele faz fast-forward/merge quando não há conflito, abre PR quando há, e os workflows de release herdados do upstream ficam desligados no fork.

## Objetivo

O diagnóstico mostrou que os três forks são **espelhos sem customização** que ficaram 2 a 5 commits atrás (um deles é correção de CVE) sem ninguém perceber. Manter isso à mão não escala. A meta é que o atraso seja detectado em até 24h e resolvido sem intervenção humana quando não houver conflito. Também é preciso que os workflows de release do upstream não rodem no fork (publicação no PyPI/GHCR/gh-pages com identidade errada ou falha ruidosa).

## Escopo

**DENTRO:**
- Novo arquivo `.github/workflows/fork-sync-upstream.yaml` em **cada** fork (diode, diode-sdk-python, diode-netbox-plugin):
  - `on: schedule` (diário, 06:00 BRT) + `workflow_dispatch`.
  - Guarda `if: github.repository_owner != 'netboxlabs'` (o arquivo nunca vai para o upstream, mas a guarda evita surpresa se for).
  - Para cada branch em `[develop, release]` existente no fork: chamar `POST /repos/{fork}/merge-upstream` (`gh api`).
    - `merge_type` `fast-forward` ou `merge` → sucesso, nada a fazer.
    - HTTP 409 (conflito) → criar branch `sync/upstream-<branch>-<data>` a partir de `upstream/<branch>` e abrir PR para `<branch>` com label `upstream-sync` e o corpo listando `git log --oneline <branch>..upstream/<branch>`.
  - Identidade: GitHub App ou fine-grained PAT com `contents:write`, `pull_requests:write` e **`workflows:write`** (ver Riscos), guardada em `secrets.FORK_SYNC_TOKEN`.
- Desabilitar no fork, via configuração (sem editar arquivo), os workflows de publicação herdados:
  - diode: `server-release.yaml`, `helm-release.yaml`.
  - sdk: `release.yaml`.
  - plugin: `release.yaml`.
  - Comando: `gh workflow disable <file> -R aliothbl/<repo>`. Registrar no `specs/features/upstream-sync.md` de cada repo.
- Verificação de atraso no PR: job `fork-upstream-drift` (no mesmo arquivo, `on: pull_request`) que só **avisa** (`::warning`) quando `develop` está atrás do upstream.
- Seção "Sincronização com upstream" em `specs/README.md` de cada repo, com o runbook manual (os comandos das SPECs 2130/2131/2132) para quando o PR de conflito abrir.

**FORA:**
- Rebase automático de customizações. Hoje não há nenhuma. Quando houver, o PR de conflito é o mecanismo.
- Espelhar branches de trabalho do upstream (`poc/*`, `bug/*`, `fix/*`).
- Publicação de artefatos próprios do fork (imagem/pacote). Se um dia for necessária, SPEC própria com nome de pacote/registro distinto.

## Invariantes
- NUNCA editar arquivo que exista no upstream para viabilizar a automação. Só arquivos novos (`fork-*.yaml`, `specs/**`).
- NUNCA force-push em `develop`/`release` do fork.
- SEMPRE que houver conflito, um humano revisa (PR). A automação nunca resolve conflito sozinha.

## Implementação

Esqueleto do job principal:

```yaml
name: fork-sync-upstream
on:
  schedule: [{cron: "0 9 * * *"}]   # 06:00 BRT
  workflow_dispatch: {}
permissions: {}
jobs:
  sync:
    if: github.repository_owner != 'netboxlabs'
    runs-on: ubuntu-latest
    strategy: {matrix: {branch: [develop, release]}, fail-fast: false}
    steps:
      - name: merge-upstream
        env: {GH_TOKEN: "${{ secrets.FORK_SYNC_TOKEN }}"}
        run: |
          set -euo pipefail
          gh api repos/${{ github.repository }}/branches/${{ matrix.branch }} >/dev/null 2>&1 || exit 0
          if gh api -X POST repos/${{ github.repository }}/merge-upstream -f branch=${{ matrix.branch }} > out.json 2> err.txt; then
            jq -r '"\(.merge_type): \(.message)"' out.json
          elif grep -q 409 err.txt; then
            echo "conflict=true" >> "$GITHUB_OUTPUT"
          else
            cat err.txt; exit 1
          fi
      # passo condicional: checkout com fetch-depth 0, add upstream, criar branch sync/... e `gh pr create`
```

- Actions pinadas por SHA (alinhado a SPEC-20261113-2152).
- O diode não tem branch `release` no fork hoje. O `exit 0` cobre esse caso.

### Modelo de dados
—

<!-- Alternativas:
- Bot "Pull" (wei/pull) / Repo Sync apps: rejeitado — dependência externa com acesso de escrita, e o comportamento de hard-reset padrão apagaria `specs/`.
- `git merge` local no runner + push: rejeitado como caminho principal — o endpoint merge-upstream faz o mesmo server-side com menos permissões e menos código; fica como fallback no passo de conflito.
- Manter `specs/` em branch separada (`fork/specs`) para develop ficar FF puro: rejeitado — specs precisam versionar junto do código que descrevem; os paths são disjuntos, então o merge é sempre limpo.
-->

## Riscos
- **Commits do upstream que alteram `.github/workflows/*` não podem ser enviados com `GITHUB_TOKEN`** (o GitHub exige a permissão `workflows`). Mitigação: usar App/PAT com `workflows:write`. Sem isso, o merge-upstream falha justamente nos bumps de actions.
- Confirmar na ativação que `merge-upstream` faz merge commit (não só FF) quando o fork tem commits próprios em `specs/`. A documentação indica `merge_type: merge`. Se não fizer, o fallback é o passo de PR.
- Push feito por App/PAT dispara os workflows de CI do fork (desejável). Os de release estão desabilitados por configuração, o que precisa ser checado após cada sync, porque o upstream pode **adicionar** um workflow de publicação novo. Mitigação: o job de drift lista workflows habilitados que contêm `pypi-publish|docker/build-push|helm` e emite warning.

## Sinais de sucesso
- Em 30 dias, nenhum dia com `develop` do fork mais de 24h atrás de `upstream/develop` sem um PR de sync aberto.

## Critério de aceite
- [ ] `fork-sync-upstream.yaml` presente nos 3 forks; `workflow_dispatch` manual resulta em `fast-forward`/`merge` ou "already up to date" | verify: `gh run list -R aliothbl/diode -w fork-sync-upstream -L 1 --json conclusion -q '.[0].conclusion'`
- [ ] Simulação de conflito (commit local tocando um arquivo que o upstream alterou, em repo de teste) abre PR com label `upstream-sync` | evidence: manual @aliothbl
- [ ] Workflows de release desabilitados nos 3 forks | verify: `for r in diode diode-sdk-python diode-netbox-plugin; do gh workflow list -R aliothbl/$r --all --json name,state -q '.[]|select(.name|test("release";"i"))|.state'; done`
- [ ] Runbook "Sincronização com upstream" em `specs/README.md` dos 3 repos | evidence: manual @aliothbl
