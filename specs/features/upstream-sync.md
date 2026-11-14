# Feature: upstream-sync

**Keywords:** fork, upstream, netboxlabs, fast-forward, merge, tags, release-branch, divergence, specs-dir
**Arquivos principais:**
  - .git/config (remote `upstream` → https://github.com/netboxlabs/diode.git)
  - specs/ (única customização do fork)
  - .github/workflows/ (guards de fork — SPEC-20261113-2152)
**Resumo:** Processo de manter `aliothbl/diode` alinhado com `netboxlabs/diode`, preservando as customizações locais, que hoje se resumem ao diretório `specs/`.

## Specs desta feature
### Concluídas
—
### Planejadas (future/)
- SPEC-20261113-2130 | Sincronizar fork com upstream (develop, nov/2026) | 3 commits de atraso, incluindo CVE (#598)
- SPEC-20261113-2140 | Automação recorrente do sync upstream | evitar que o atraso volte a acumular

## Estado atual
Em 2026-11-13, `develop` do fork tem **0 commits próprios** e está **3 commits atrás** de `upstream/develop` (`7cb8d6d` #598 Go 1.26.10/x-net CVE, `175bddd` #600 `tags_resolved`, `519ccec` #601 `source_match` no hash de dedup). O merge-base é `054e7af` (2026-09-25). Contra `upstream/release` o atraso é de 34 commits. O fork não publica branch `release`. `upstream/main` é legado: o fork está 1160 commits à frente dele.

O remote `upstream` foi adicionado no clone local em 2026-11-13 (`git remote add upstream https://github.com/netboxlabs/diode.git`). As branches de trabalho do upstream (`poc/*`, `bug/*`, `fix/obs-3116-*`, `ingestion-logs-stats-split`, `obs-399-*`) não são espelhadas.

Acoplamentos cross-repo relevantes para o sync: #600 (server) ↔ plugin #227 (`tags_resolved`). Ambos são aditivos (`omitempty`), mas só funcionam ponta a ponta juntos.

## Decisões arquiteturais ativas
- DEC-20261113-2130-specs-fora-de-docs [ativa] (SPEC-20261113-2130) — o processo SDD vive em `specs/` e não em `docs/`, porque `docs/` é o diretório do mkdocs publicado pelo upstream (`mkdocs.yml`, `nav:`). Paths disjuntos garantem merge sem conflito.
- DEC-20261113-2131-upstream-first [ativa] (SPEC-20261113-2130) — correções de código vão primeiro como PR para `netboxlabs/diode`. O fork carrega o commit só até o merge upstream, e no sync o commit local é descartado se foi absorvido.

## Alternativas consideradas e rejeitadas
—

## Gotchas
- SPEC-20261113-2130 | #601 muda o valor do `entity_hash` (2026-11-13 21:30) — após o deploy, entidades já vistas são replanejadas uma vez. Fazer deploy fora de pico.
- SPEC-20261113-2130 | `server-release.yaml` chama workflow reutilizável de `netboxlabs/diode@develop` (2026-11-13 21:30) — mesmo no fork, roda código do upstream. Ver SPEC-20261113-2152.
