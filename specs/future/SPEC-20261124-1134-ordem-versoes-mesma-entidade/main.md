# SPEC-20261124-1134: Versão mais velha da mesma entidade não pode sobrescrever a mais nova

**Status:** draft
**Porte:** G
**Owner:** @aliothbl
**Criada:** 2026-11-24 11:34
**Ativada:** —
**Pausada em:** —
**Concluída:** —
**Commit final:** —
**Keywords:** lost update, ordem de aplicação, concorrência, AUTO_APPLY_PROCESSOR_CONCURRENCY, ClaimQueuedForAutoApply, bulk-plan-apply, last_seen, observed_at, stale write, matcher
**Features:** reconciler, persistence
**Branch:** _(a definir na ativação)_
**Depende de:** — (cross-repo: SPEC par no `diode-netbox-plugin`, a criar na ativação se a opção B for a escolhida)
**Programa:** —
**Relacionadas:** SPEC-20261124-0648-carga-17k-eventos-por-minuto (onde o problema apareceu); `review/README.md` (recomendação de concorrência 4 como default).
**Impacto:** alto · **Complexidade:** alta
**Estratégia upstream:** upstream-first. O upstream tem o mesmo risco com `AUTO_APPLY_PROCESSOR_CONCURRENCY > 1`. Proposta para a NetBox Labs só depois do retorno ao review atual e com novo OK do owner.
**Origem:** SPEC-0648, 1ª execução `sustained` a 17.000/min com c=4 (`bench/results/20261124-083224-candidate-sustained-run1.json`, journal da 0648 em 2026-11-24 09:52). Causa confirmada no `sustained` reduzido: c=1 → 0 e 0 descrições erradas; c=4 → 25 e 2 (`bench/results/20261124-113136-*`, `20261124-121002-*`).
**Resumo:** Com c=4, o reconciler aplica batches vizinhos da fila em paralelo. Quando duas versões da mesma entidade (conteúdos diferentes, então linhas diferentes) caem em batches concorrentes, a mais velha pode gravar por último no NetBox. No bench, 24 de 6.241 interfaces atualizadas terminaram com uma descrição mais antiga que a última enviada. Em todos os casos as duas versões chegaram a ≤ ~9 s uma da outra, o que cabe nas 400 linhas em voo (c=4 × batch 100).

## Objetivo

Garantir que, para cada objeto do NetBox, o estado final corresponde à observação **mais recente** que o Diode recebeu dele, com qualquer valor de `AUTO_APPLY_PROCESSOR_CONCURRENCY`, sem voltar a aplicar tudo em série.

## Contexto

- `ClaimQueuedForAutoApply` (`dbstore/postgres/queries/ingestion_logs.sql`) pega as linhas QUEUED em ordem de `id`, com `FOR UPDATE SKIP LOCKED`. Cada worker leva um batch e manda um `POST /bulk-plan-apply/`. A ordem entre batches concorrentes não é garantida.
- O lock de dedup do M-1 (`WithDedupLocks`) é por **hash do conteúdo**. Duas versões diferentes da mesma entidade têm hashes diferentes e não se serializam.
- O reconciler **não tem chave de identidade** da entidade no caminho de auto-apply: só o hash do conteúdo (`entityhash`); `external_id` é um UUID por linha. Quem resolve identidade é o matcher do plugin (`netbox_diode_plugin/api/matcher.py`). O `entitymatcher` do diode-server serve ao serviço de grafo, que é opcional.
- O `BulkPlanApplyRequest` (`netboxdiodeplugin/client.go:445`) leva só as entidades. Nenhum tempo de observação chega ao plugin.
- **Qual tempo conta como "mais recente":** não é o `id` nem o `created_at` da linha. Uma linha antiga reenfileirada por um repetido (`BulkMarkDuplicates` atualiza `last_seen`) é uma observação **nova** daquele conteúdo. O tempo certo é o da última observação do conteúdo (`last_seen`, ou o `ingestion_ts` do repetido).

## Opções

**A — chave de identidade no reconciler (Go).**
- Derivar, por tipo de objeto, uma chave de ordem a partir dos campos que o matcher do plugin usa (ex.: interface = device + nome; IP = endereço + VRF; device = nome + site).
- Usar a chave para ordenar: no claim, não pegar uma linha se outra com a mesma chave e observação mais velha ainda estiver QUEUED/APPLYING; ou marcar a versão velha como superada (novo estado) quando chega uma nova.
- Prós: tudo no diode-server, sem mudar a API do plugin.
- Contras: duplica a lógica de identidade do plugin em Go para ~100 tipos. Se a chave for fina demais, deixa passar o problema. Grossa demais só perde paralelismo, então a chave tem que errar para o lado grosso. Mantê-la em sincronia com o matcher é um custo permanente.

**B — o plugin rejeita escrita velha (recomendada).**
- O reconciler manda, por entidade, o tempo de observação (`observed_at`, de `last_seen`).
- O plugin, depois que o matcher resolve o objeto, guarda por objeto (`content_type`, `object_id`) o maior `observed_at` aplicado, numa tabela própria, com lock da linha dentro da transação do apply. Uma entidade com `observed_at` mais velho que o guardado não grava: volta como "superada", e o reconciler a marca como NO_CHANGES com motivo.
- Prós: a identidade é a do próprio matcher, que é a autoridade. Vale para qualquer tipo e para qualquer número de workers ou réplicas do NetBox (combina com a fase 5 da 0648). Não perde paralelismo.
- Contras: mudança de API (campo opcional novo) e de schema no plugin (migration). Precisa de negociação de versão: reconciler novo com plugin velho continua como hoje.

## Escopo (ajustar depois da escolha entre A e B)

**DENTRO:**
- Teste que reproduz o problema sem o bench: duas versões da mesma interface em batches concorrentes; hoje a velha pode vencer, depois da correção a nova sempre vence.
- Correção pela opção escolhida, com métricas (ex.: `diode_reconciler_superseded_total`).
- Repetir o `sustained` da 0648 com c=4 e c=8: 0 descrições erradas.
- Atualizar o `review/README.md`: a recomendação de concorrência 4 passa a citar este achado e a correção.

**FORA:**
- Ordem entre entidades **diferentes** (ex.: IP antes da interface): já tratada pelo create-or-find do plugin.
- Repetidos que trazem de volta conteúdo velho de propósito (o produtor mandou de novo): com o tempo de observação certo, isso é uma observação nova e deve vencer.

## Invariantes
- NUNCA a observação mais velha de um objeto grava depois da mais nova, com qualquer concorrência.
- SEMPRE compatível: um lado novo com o outro velho se comporta como hoje.
- NUNCA serializar tudo: o throughput com c=4 não pode cair mais que o ruído medido (~9%).

## Riscos
- Relógio: `last_seen` vem do relógio do Postgres do Diode, um só. Não usar o relógio do produtor.
- Opção B: a tabela de `observed_at` cresce com o número de objetos e precisa de limpeza junto com o objeto.
- Opção A: chave de identidade errada falha em silêncio. Mitigação: teste que confronta a chave com o matcher do plugin.

## Critério de aceite
- [ ] Opção escolhida (A ou B) registrada como DEC pelo owner | evidence: manual @aliothbl
- [ ] Teste que reproduz a gravação fora de ordem falha antes e passa depois | evidence: manual @aliothbl
- [ ] Suítes verdes (diode-server; plugin se B) | verify: `cd diode-server && go test ./...`
- [ ] `sustained` (0648) com c=4 e c=8: 0 descrições erradas, 3/3 execuções cada | evidence: manual @aliothbl
- [ ] Throughput com c=4 dentro do ruído do antes | evidence: manual @aliothbl
- [ ] `review/README.md` atualizado | evidence: manual @aliothbl
