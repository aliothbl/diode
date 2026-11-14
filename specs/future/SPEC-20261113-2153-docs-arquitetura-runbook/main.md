# SPEC-20261113-2153: Documentação de arquitetura, runbook operacional e modelo de ameaça

**Status:** draft
**Porte:** P
**Owner:** @aliothbl
**Criada:** 2026-11-13 21:53
**Ativada:** —
**Pausada em:** —
**Concluída:** —
**Commit final:** —
**Keywords:** docs, architecture, runbook, backpressure, applying, pel, dlq, threat-model, ingress-auth, mkdocs
**Features:** docs
**Branch:** _(a definir na ativação)_
**Depende de:** SPEC-20261113-2130-sync-upstream-develop-2026-11
**Programa:** —
**Impacto:** baixo · **Complexidade:** baixa
**Estratégia upstream:** upstream-first para `docs/` (arquitetura e runbook são úteis a todos); notas específicas do fork ficam em `specs/`
**Origem:** diagnóstico 2026-11-13 (specs/DIAGNOSTICO-2026-11-13.md, achado D-23)
**Resumo:** `docs/` cobre só proto, métricas, observability e getting-started. Faltam: diagrama e descrição de arquitetura (SDK → ingester → Redis → reconciler → plugin → NetBox); um runbook para backpressure travado, linhas presas em APPLYING, PEL órfão e DLQ; e um modelo de ameaça explícito, já que hoje a autenticação do gRPC depende do ingress.

## Objetivo

Quem opera o Diode deve conseguir diagnosticar e recuperar os incidentes conhecidos sem ler código, e entender a fronteira de confiança.

## Escopo

**DENTRO:**
- `docs/architecture.md`, com:
  - serviços (`diode-server/cmd/*`) e as portas de cada um;
  - fluxo de dados;
  - modos AutoApply vs IngestionLogProcessor (`cmd/reconciler/main.go:211-244`);
  - branching (`reconciler/ops.go:26`, refresh de 5 min, fail-closed da #586);
  - dedup por hash;
  - diagrama Mermaid.
- `docs/runbook.md`, com:
  - **Backpressure travado**: `XLEN diode.v1.ingest-stream` > `INGESTION_LOG_PROCESSOR_BACKPRESSURE_THRESHOLD` (100). Como inspecionar `XINFO GROUPS`/`XPENDING` e como drenar.
  - **Linhas em APPLYING**: consulta `SELECT count(*) FROM ingestion_logs WHERE state = 8`, comportamento do reset no startup (`queries/ingestion_logs.sql:193-199`) e, após a SPEC-20261113-2144, o lease.
  - **PEL órfão e DLQ**: após a SPEC-20261113-2143, `XRANGE` da DLQ e replay manual com `XADD`.
  - **Redis em maxmemory**: `ResourceExhausted` no ingester (`ingester/component.go:282-342`).
  - **Troca de hash** (#601 e futuras): reprocessamento esperado.
- `docs/security.md` (modelo de ameaça), com:
  - a autenticação gRPC depende do `auth_request` do nginx / da anotação `auth-url` do ingress (`charts/diode/templates/ingress-grpc.yaml:15`) até a SPEC-20261113-2142;
  - o plugin valida por introspecção;
  - os segredos do chart (`diode-secrets.yaml`).
- Adicionar as páginas ao `nav:` de `mkdocs.yml:37`.

**FORA:**
- Documentação do SDK e do plugin (repos próprios).

## Invariantes
- SEMPRE citar caminho de arquivo e variável de ambiente reais (nada genérico).
- NUNCA incluir segredo real em exemplo (usar `<REDACTED:tipo>`).

## Implementação
Markdown no padrão do `docs/` existente. Comandos do runbook testados contra o `docker-compose` de dev.

### Modelo de dados
—

## Riscos
- O runbook desatualiza quando as SPECs 2143/2144 entrarem. Mitigação: cada uma dessas SPECs inclui no critério de aceite a atualização do runbook.

## Sinais de sucesso
- Um incidente simulado (Postgres fora + restart) é resolvido seguindo só o runbook.

## Critério de aceite
- [ ] `docs/architecture.md`, `docs/runbook.md` e `docs/security.md` criados e no `nav` do mkdocs ← specctl check
- [ ] `mkdocs build --strict` sem erro ← specctl check
- [ ] Cada procedimento do runbook executado uma vez no compose de dev ← specctl check
