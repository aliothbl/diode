# Journal — SPEC-20261124-1134-ordem-versoes-mesma-entidade

## SNAPSHOT
- **Fase:** implementação escrita nos dois repos e commitada; validação incompleta (pausa de 2026-11-24).
- **Branches:** `spec/20261124-1134-ordem-versoes` no diode e no diode-netbox-plugin. Só local.
- **Próximo passo:** rodar a suíte completa do plugin (v4.7.0) e `makemigrations --check`; investigar `TestIngestionProcessor_DedupKeysFollowSourceMatch` (pré-existente); depois `sustained` com c=4/c=8 ×3.

## Decisões
- **DEC-1 — opção B (2026-11-24 17:56).** Owner escolheu "B: plugin recusa escrita velha". A identidade vem do matcher do plugin.
- **DEC-2 — duas defesas, não uma (2026-11-24 17:56).** Lendo o apply, há dois caminhos para o update perdido, e a opção B como escrita só cobria o primeiro:
  1. **Ordem:** a observação mais velha aplica depois da mais nova.
  2. **Plan velho:** o UPDATE grava o estado mesclado (`_partially_merge`: o objeto como estava no plan + o que chegou), com `partial=True`. Se outro lote mudou o objeto entre o plan e o apply, o UPDATE devolve valores velhos de campos que nem queria mudar. Isso acontece mesmo com as versões na ordem certa.
  - Defesa 1, **checagem otimista**: no plan, guardar o `last_updated` de cada objeto existente; no apply, `select_for_update()` no objeto e, se o `last_updated` mudou, refazer plan + apply da entidade com o estado atual (até 3 tentativas; depois, erro de apply `concurrent_modification`). Vale com ou sem `observed_at`. O `last_updated` (auto_now do NetBox) muda em todo `save()`, inclusive edição pela UI.
  - Defesa 2, **ordem por campo**: o reconciler manda `observed_at` (ms, de `ingestion_logs.last_seen`) por entidade. O plugin guarda, por objeto e campo, o maior `observed_at` aplicado (`ObjectObservation`: branch, object_type, object_id, fields jsonb). No UPDATE, sob o lock do objeto, um campo **afirmado** pela entidade cujo `observed_at` guardado é mais novo não é gravado. Afirmado = chave presente na entidade que chegou, antes da mescla. Por campo e não por objeto porque entidades diferentes escrevem no mesmo objeto (o IP traz a interface aninhada sem descrição; uma trava por objeto bloquearia a descrição legítima).
  - A checagem e a ordem só valem no `/bulk-plan-apply/` (auto-apply). O fluxo manual (`/bulk-plan` + `/apply-change-set`) fica como está. Os campos novos do `Change` não vão para o JSON da resposta.
  - A tabela não participa do branching (model simples, como `Setting`) e tem o schema do branch na chave.
  - **Limitações aceitas na v1:**
    - Uma observação mais nova que não muda nada (NOOP) não registra o `observed_at`. Uma mais velha que chegue depois ainda grava, o que só importa para valor que oscila (A→B→A).
    - CREATE contra CREATE da mesma entidade continua com o create-or-find de hoje.
    - Ambas ficam registradas para uma etapa futura.

## LOG
- 2026-11-24 17:56 — SPEC ativada; branches criados nos dois repos. Causa confirmada antes da ativação (journal da 0648, c=1 → 0/0, c=4 → 25/2).
- 2026-11-24 18:25 — **Implementação.**
  - Plugin (branch `spec/20261124-1134-ordem-versoes`):
    - `api/apply_guard.py` (novo): contextvar `apply_guard`, `StalePlan`, `get_for_update` (select_for_update + comparação do `last_updated` do plan), `drop_superseded_fields`, `still_writes`, `record_observation`.
    - `models.ObjectObservation` + migration `0009`.
    - `Change.planned_last_updated`/`Change.asserted` (não serializados), preenchidos em `differ._guard_facts`.
    - `applier._update_by_id` (o UPDATE por `object_id` passa pelas proteções).
    - `BulkPlanApplyView._process_entity`: laço de re-plan (até 3; a 4ª tentativa aplica sem a checagem otimista, como antes) com limpeza dos caches de request e do cache entre requests do objeto.
    - Decisão de implementação: **CREATE não registra observação.** Quando o create-or-find cai num objeto existente, ele pode não aplicar o payload, e registrar a observação bloquearia uma escrita legítima mais tarde.
  - Reconciler (branch `spec/20261124-1134-ordem-versoes`): `QueuedIngestionLog.ObservedAt` (de `last_seen`, nos dois claims); `BulkPlanEntity.ObservedAt` (`observed_at`, ms, omitempty), enviado só no `BulkPlanApply`.
  - Testes:
    - Plugin `test_apply_guard.py`: 7 testes OK (`-k ApplyGuard`, NetBox v4.7.0).
    - **Sem a correção** (stash de views/applier/differ/common/matcher), os dois testes centrais falham: `'older' != 'newer'` e `1 != 2` (sem re-plan).
    - Go `TestOpsBulkPlanApplySendsObservedAt` OK.
  - Em andamento: suíte completa do plugin (v4.7.0) e `go test ./...`.
- 2026-11-24 18:29 — **Pausa a pedido do owner** ("vamos comitar tudo no meu fork e parar por hoje"). Estado:
  - `go test ./...` no diode-server: 1 falha, `TestIngestionProcessor_DedupKeysFollowSourceMatch` (expectativas do mock não atendidas em 2 s). **Pré-existente:** falha igual 5/5 sem esta mudança (stash + teste novo removido). Não investigada; os outros pacotes e `TestOpsBulkPlanApplySendsObservedAt` passam.
  - Suíte completa do plugin (v4.7.0): **interrompida** pela pausa, sem nenhum FAIL/ERROR até ali. Precisa rodar inteira de novo.
  - Falta: suíte do plugin completa; `makemigrations --check`; `sustained` (0648) com c=4 e c=8 ×3 para 0 descrições erradas; throughput antes/depois; `review/README.md`.
