# SPEC-20261113-2145: Retry seguro nas chamadas de apply ao plugin

**Status:** draft
**Porte:** M
**Owner:** @aliothbl
**Criada:** 2026-11-13 21:45
**Ativada:** —
**Pausada em:** —
**Concluída:** —
**Commit final:** —
**Keywords:** retryablehttp, checkretry, idempotency-key, bulk-plan-apply, timeout, transport-error, default-branch, 404, backoff
**Features:** reconciler
**Branch:** _(a definir na ativação)_
**Depende de:** SPEC-20261113-2130-sync-upstream-develop-2026-11, SPEC-20261113-2144-lease-claims-applying, `diode-netbox-plugin/specs/future/SPEC-20261113-2210-concorrencia-otimista-apply` (409 e idempotência no plugin)
**Programa:** `specs/programs/diode-stack-hardening.md`
**Impacto:** médio-alto · **Complexidade:** média
**Estratégia upstream:** upstream-first — abrir PR em netboxlabs/diode (e o par no plugin); carregar no fork só até o merge; ao sincronizar, descartar o commit local se o upstream absorveu
**Origem:** diagnóstico 2026-11-13 (specs/DIAGNOSTICO-2026-11-13.md, achados D-7, D-8 e D-14)
**Resumo:** O cliente HTTP do plugin usa a política padrão do retryablehttp. Ela repete `POST /bulk-plan-apply/` em timeout ou 5xx, com 5 s de `ResponseHeaderTimeout`, mesmo quando o NetBox ainda está aplicando o primeiro envio. Uma falha de transporte marca o lote inteiro como FAILED sem retry posterior. E um 404 transitório em `/default-branch/` vira "sem branch" até o próximo refresh, o que manda o apply para a main.

## Objetivo

Tornar o caminho de apply **no máximo uma vez por tentativa lógica** e **eventualmente aplicado** em falhas transitórias:
- Nenhum POST de escrita é reenviado às cegas.
- Falhas de transporte voltam para a fila com backoff, em vez de FAILED definitivo.
- O branch alvo nunca muda em silêncio por causa de um erro transitório.

## Escopo

**DENTRO:**
- `diode-server/netboxdiodeplugin/client.go:207-214`:
  - `rhttp.CheckRetry` customizado. Repete GET (`/default-branch/`) e erros de conexão **antes do envio** (dial/TLS). Para POST de apply (`/bulk-plan-apply/`, `/apply-change-set/`), **não** repete após o envio, a menos que o request carregue `Idempotency-Key` e o plugin anuncie suporte.
  - Timeout separado para apply: `NETBOX_DIODE_PLUGIN_APPLY_TIMEOUT_SECONDS` (default 60). O `ResponseHeaderTimeout` de 5 s (`:47-48`, `:190-195`) continua valendo para plan/GET.
- Header `Idempotency-Key: <ingestion_log ids hash + tentativa lógica>` em `BulkPlanApply` (`:409-470`). O plugin (SPEC par) guarda as chaves recentes no cache e responde 200 com o resultado anterior em duplicata.
- `reconciler/ops.go:640-647` (falha de lote inteiro → FAILED): trocar por um estado retryable. Reaproveitar QUEUED com `attempts` (coluna nova) e `next_attempt_at`. Depois de `AUTO_APPLY_MAX_ATTEMPTS` (default 5), vai para FAILED com o último erro.
- `reconciler/ops.go:147-153` (`ErrDefaultBranchNotFound` → `setBranch(nil)`): diferenciar "rota inexistente" (plugin antigo; ex.: 404 com corpo HTML do Django, ou sem o header `X-Diode-Plugin-Version`) de "recurso não encontrado" (JSON do plugin). Só no primeiro caso gravar nil permanente. No segundo, manter o último valor e emitir a métrica `diode_reconciler_default_branch_refresh_errors_total`.
- Corrigir o User-Agent: `SDKVersion = "0.1.0"` (`client.go:39`, TODO) → `version.Release()`.

**FORA:**
- Implementação da idempotência e do 409 no plugin (SPEC par no `diode-netbox-plugin`).
- Retry do `IngestionLogProcessor` (modo manual): o usuário reaplica pela UI.

## Invariantes
- NUNCA reenviar automaticamente um POST de escrita sem chave de idempotência aceita pelo plugin.
- SEMPRE manter o branch alvo anterior quando o refresh falhar por motivo transitório.
- NUNCA marcar FAILED definitivo por erro de transporte antes de esgotar `AUTO_APPLY_MAX_ATTEMPTS`.

## Implementação

- Negociação de capacidade: o plugin passa a responder `X-Diode-Plugin-Capabilities: idempotency` em `/default-branch/`, que já é chamado a cada 5 min (`reconciler/ops.go:26`). O cliente guarda essa flag. Sem ela, nada de retry em POST.
- O claim da SPEC-20261113-2144 passa a filtrar `next_attempt_at <= now()`.
- Teste com `httptest.Server`:
  - timeout no 1º POST → nenhum 2º POST sem a capacidade;
  - com a capacidade → um 2º POST com a mesma chave;
  - 503 de lote → linhas QUEUED com `attempts=1`;
  - 404 HTML vs 404 JSON em default-branch.

### Modelo de dados
- `ingestion_logs.attempts int NOT NULL DEFAULT 0`, `ingestion_logs.next_attempt_at timestamptz NULL` (migration goose, reversível) e o ajuste das queries de claim.

<!-- Alternativas: (a) RetryMax=0 para tudo — rejeitada: perde a resiliência legítima de GET e de falhas pré-envio; (b) aumentar só o timeout — rejeitada: reduz mas não elimina o duplo apply; (c) usar o ID do change set como chave sem coluna attempts — parcialmente adotada (a chave é derivada dos ids), mas attempts/next_attempt_at continuam necessários para o backoff. -->

## Riscos
- Um plugin desatualizado no fork não anuncia a capacidade, e o retry de POST fica desligado (comportamento seguro, porém menos resiliente). Mitigação: subir o plugin primeiro.
- `attempts` interage com o requeue de duplicados (`requeued_from_state`). Mitigação: zerar `attempts` no requeue e cobrir com teste.

## Sinais de sucesso
- Em injeção de latência de 10 s no NetBox, nenhum objeto aparece duas vezes no changelog do NetBox para a mesma ingestão. Um 503 de 30 s termina em APPLIED, sem FAILED.

## Critério de aceite
- [ ] `CheckRetry` customizado com testes de POST sem/com capacidade | verify: `cd diode-server && go test -race -run 'Retry|Idempot' ./netboxdiodeplugin/...`
- [ ] Falha de transporte de lote → QUEUED com backoff até `AUTO_APPLY_MAX_ATTEMPTS` | verify: `cd diode-server && go test -race -run 'BulkPlanApply.*Transport' ./reconciler/...`
- [ ] 404 transitório de default-branch preserva o branch anterior | verify: `cd diode-server && go test -race -run 'DefaultBranch' ./reconciler/...`
- [ ] User-Agent com a versão real do reconciler | verify: `cd diode-server && ! grep -n 'SDKVersion = "0.1.0"' netboxdiodeplugin/client.go`
- [ ] Teste ponta a ponta com o plugin da SPEC par: latência injetada sem duplicata no changelog | evidence: manual @aliothbl
