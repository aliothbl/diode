# Feature: docs

**Keywords:** mkdocs, docs, getting-started, diode-proto, metrics, observability, architecture, runbook, threat-model
**Arquivos principais:**
  - mkdocs.yml
  - docs/
  - GET_STARTED.md
  - README.md
**Resumo:** Documentação pública do upstream (mkdocs): getting started, referência do proto e métricas. Ainda não há arquitetura, runbook operacional nem modelo de ameaça.

## Specs desta feature
### Concluídas
—
### Planejadas (future/)
- SPEC-20261113-2153 | Arquitetura, runbook e modelo de ameaça | lacuna para operar e diagnosticar incidentes

## Estado atual
`docs/` contém `index.md`, `getting-started.md`, `diode-proto.md`, `metrics.md`, `observability/`, `protobuf/` e `license.md`, publicados via `mkdocs.yml` (com `nav:`). Não há descrição do fluxo ponta a ponta, dos modos AutoApply/manual, do branching, do backpressure nem de procedimentos de recuperação.

## Decisões arquiteturais ativas
- DEC-20261113-2130-specs-fora-de-docs [ativa] — documentação de processo do fork em `specs/`. `docs/` é exclusivo do conteúdo publicável (upstream-first).

## Alternativas consideradas e rejeitadas
—

## Gotchas
—
