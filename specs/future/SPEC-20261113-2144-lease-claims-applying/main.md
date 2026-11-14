# SPEC-20261113-2144: Lease nos claims de ingestion_logs (fim do reset global de APPLYING)

**Status:** draft
**Porte:** M
**Owner:** @aliothbl
**Criada:** 2026-11-13 21:44
**Ativada:** —
**Pausada em:** —
**Concluída:** —
**Commit final:** —
**Keywords:** applying, claim, lease, skip-locked, rolling-update, replicas, duplicate-apply, goose, sqlc, recreate
**Features:** reconciler, persistence, deploy-helm
**Branch:** _(a definir na ativação)_
**Depende de:** SPEC-20261113-2130-sync-upstream-develop-2026-11, SPEC-20261113-2141-graceful-shutdown-sigterm
**Programa:** `specs/programs/diode-stack-hardening.md`
**Impacto:** alto · **Complexidade:** média
**Estratégia upstream:** upstream-first — abrir PR em netboxlabs/diode; carregar no fork só até o merge; ao sincronizar, descartar o commit local se o upstream absorveu
**Origem:** diagnóstico 2026-11-13 (specs/DIAGNOSTICO-2026-11-13.md, achado D-6)
**Resumo:** `ResetApplyingIngestionLogs` faz `UPDATE ... SET state=1 WHERE state=8` sem dono, em todo startup. Num rolling update, ou com `replicaCount>1`, um pod novo devolve à fila linhas que o pod antigo ainda está aplicando, e o mesmo lote é aplicado duas vezes no NetBox. Substituir por lease (`claimed_by`, `claimed_until`). Como mitigação imediata, usar `strategy: Recreate` no reconciler.

## Objetivo

Garantir que cada ingestion_log em APPLYING tenha um único dono vivo, e que só linhas de donos mortos (lease vencido) voltem para QUEUED. Hoje:
- `ClaimQueuedForAutoApply` (`dbstore/postgres/queries/ingestion_logs.sql:178-191`) muda QUEUED (1) → APPLYING (8) com `FOR UPDATE SKIP LOCKED`, sem registrar o dono.
- `ResetApplyingIngestionLogs` (`:193-199`) é chamado em `cmd/reconciler/main.go:227-230` antes do registro do `AutoApplyProcessor`.
- O chart usa a estratégia padrão do Deployment (RollingUpdate) em `charts/diode/templates/diode-reconciler-deployment.yaml`, com `replicaCount: 1` (`values.yaml:257`). Durante o rollout, os dois pods coexistem.

## Escopo

**DENTRO:**
- **Mitigação imediata** (pode ser o primeiro commit, mergeável sozinho): `diodeReconciler.strategy` em `values.yaml` com default `{type: Recreate}`, aplicado no template do deployment do reconciler.
- Migration goose `dbstore/postgres/migrations/<ts>_add_ingestion_log_claim_lease.sql`: adicionar `claimed_by text NULL` e `claimed_until timestamptz NULL` em `ingestion_logs`, mais o índice parcial `(claimed_until) WHERE state = 8`. O `Down` faz o drop.
- Queries:
  - `ClaimQueuedForAutoApply` e `ClaimQueuedIngestionLogs` gravam `claimed_by = @owner, claimed_until = now() + @lease`.
  - `RenewIngestionLogLeases :exec` faz `UPDATE ... SET claimed_until = now()+@lease WHERE id = ANY(@ids) AND claimed_by=@owner`.
  - `ResetExpiredApplyingIngestionLogs :exec` faz `UPDATE ... SET state=1, claimed_by=NULL, claimed_until=NULL WHERE state=8 AND claimed_until < now()`.
  - Transições finais (APPLIED/FAILED/NO_CHANGES) limpam o lease.
- `owner` = `hostname + "-" + uuid` gerado no startup do processo, para que o mesmo hostname reiniciado não herde o lease.
- `AutoApplyProcessor`: um heartbeat renova o lease a cada `lease/3` enquanto o lote está em voo. O reset de expirados roda **periodicamente** (a cada `lease`), e não só no startup.
- Remover a chamada global de `cmd/reconciler/main.go:227-230` e apagar a query antiga.
- Config: `AUTO_APPLY_CLAIM_LEASE` (default 2 min; deve ser maior que o timeout de apply da SPEC-20261113-2145).
- Regenerar sqlc (`sqlc generate` em `diode-server/`) e os mocks.

**FORA:**
- Mudança no protocolo com o plugin (idempotência fica na SPEC-20261113-2145).
- Linhas antigas já em APPLYING no momento do deploy: a migration define `claimed_until = now()` para elas, e o primeiro ciclo de reset as recupera.

## Invariantes
- NUNCA devolver à fila uma linha cujo lease ainda é válido.
- SEMPRE limpar `claimed_by`/`claimed_until` ao sair de APPLYING.
- SEMPRE manter o `Recreate` como default enquanto o lease não estiver em produção.

## Implementação

Fluxo: claim (com lease) → heartbeat → `BulkPlanApply` (`reconciler/ops.go:647`) → transição final (limpa o lease). Um pod morto para de renovar, e após `lease` qualquer réplica o recupera. A query de reset usa o índice parcial e é barata.

### Modelo de dados
- `ingestion_logs.claimed_by text NULL`, `ingestion_logs.claimed_until timestamptz NULL` e o índice `idx_ingestion_logs_applying_lease ON ingestion_logs (claimed_until) WHERE state = 8`. Migração reversível.

<!-- Alternativas: (a) advisory lock por lote — rejeitada: o lock morre com a conexão do pool, não com o worker, e não sobrevive a reconexão; (b) Recreate permanente sem lease — rejeitada como solução final: impede escalar réplicas e gera downtime a cada deploy; (c) leader election via Kubernetes Lease — rejeitada: acopla o binário ao Kubernetes e serializa todo o apply num único pod. -->

## Riscos
- Um heartbeat que falha (Postgres lento) deixa o lease vencer em meio ao apply, e outra réplica reaplica. Mitigação: lease ≥ 2× o timeout de apply. Se a renovação falhar, o processador aborta o lote após o término da chamada HTTP corrente e não grava estado final sem ser dono (`WHERE claimed_by = @owner`).
- Migration em tabela grande: `ADD COLUMN` nullable sem default é só metadado no Postgres 11+. Criar o índice com `CREATE INDEX CONCURRENTLY` em migration sem transação (`-- +goose NO TRANSACTION`).

## Sinais de sucesso
- Com `replicaCount: 2` e restarts sucessivos, nenhum ingestion_log tem dois change sets APPLIED para o mesmo lote.

## Critério de aceite
- [ ] `strategy: Recreate` default no deployment do reconciler | verify: `helm template charts/diode | grep -n "type: Recreate"`
- [ ] Migration up/down aplicam sem erro | verify: `cd diode-server && go test -tags integration_test -race -run Migration ./dbstore/...`
- [ ] Teste de integração com 2 processadores e restart de um deles: zero apply duplicado | verify: `cd diode-server && go test -tags integration_test -race -run Lease ./reconciler/...`
- [ ] `ResetApplyingIngestionLogs` global removido | verify: `cd diode-server && ! grep -rn "ResetApplyingIngestionLogs(ctx)" cmd/`
- [ ] Rolling update com 2 réplicas em ambiente de teste sem change set duplicado | evidence: manual @aliothbl
