# SPEC-20261113-2143: Recuperar PEL do stream, tratar mensagens venenosas e criar DLQ

**Status:** draft
**Porte:** M
**Owner:** @aliothbl
**Criada:** 2026-11-13 21:43
**Ativada:** —
**Pausada em:** —
**Concluída:** —
**Commit final:** —
**Keywords:** redis-streams, xreadgroup, pel, xautoclaim, xpending, dlq, poison-message, backpressure, ack-regardless, data-loss
**Features:** reconciler
**Branch:** _(a definir na ativação)_
**Depende de:** SPEC-20261113-2130-sync-upstream-develop-2026-11, SPEC-20261113-2141-graceful-shutdown-sigterm
**Programa:** —
**Impacto:** alto · **Complexidade:** média
**Estratégia upstream:** upstream-first — abrir PR em netboxlabs/diode; carregar no fork só até o merge; ao sincronizar, descartar o commit local se o upstream absorveu
**Origem:** diagnóstico 2026-11-13 (specs/DIAGNOSTICO-2026-11-13.md, achados D-3, D-4 e D-5)
**Resumo:** Três falhas no consumidor do stream `diode.v1.ingest-stream`. (1) Ninguém reclama o PEL (sem `XAUTOCLAIM`/`XPENDING`), e o nome do consumidor muda a cada pod. (2) Mensagem malformada causa panic ou derruba o loop. (3) Erro transitório do Postgres é ACKado e a mensagem é apagada (perda silenciosa). A correção preserva a garantia da #591: nada fica no stream para sempre contando no backpressure.

## Objetivo

Hoje uma mensagem entregue ao consumidor sai do caminho normal de três formas:
1. Se o processo morre entre `XReadGroup` e `XAck` (ver D-1), a entrada fica no PEL de um consumidor que nunca mais existe (`fmt.Sprintf("%s-%s", group, hostname)`, `reconciler/ingestion_processor.go:140`). O loop só lê `">"` (`:172-177`), então nunca a revê, e ela continua contando em `XLEN`. É a mesma classe de bug que a #591 corrigiu para o caminho de erro.
2. `msg.Values["request"].(string)` (`:224`) e `msg.Values["ingestion_ts"].(string)` (`:250`) são type assertions sem `ok`, e uma mensagem fora do formato causa panic. Um erro de decode/unmarshal (`:229-238`) retorna erro **antes** do ACK. O loop então devolve o erro (`:207`), o `oklog/run` derruba o processo inteiro, e a mensagem fica no PEL (cai no caso 1).
3. Se `BulkCreateIngestionLogs` falha (`:398-403`, Postgres fora), o erro é só acumulado. As linhas `:279-285` fazem ACK+XDEL "regardless of outcome" e a ingestão é perdida.

Também há um nil-deref latente em `:178-179`: `err.Error()` com `err == nil` quando `len(streams) == 0`.

## Escopo

**DENTRO:**
- **Reclaim**: um goroutine no `IngestionProcessor` chama `XAUTOCLAIM <stream> <group> <consumer> <min-idle> 0-0 COUNT 100` a cada `STREAM_RECLAIM_INTERVAL` (default 30 s, min-idle `STREAM_RECLAIM_MIN_IDLE`, default 5 min) e processa as mensagens reclamadas pelo mesmo `handleStreamMessage`.
- **Contagem de entregas**: no reclaim, consultar `XPENDING ... IDLE` para obter `times_delivered`. Acima de `STREAM_MAX_DELIVERIES` (default 5), a mensagem vai para a DLQ.
- **DLQ**: o stream `diode.v1.ingest-stream.dlq` (com `MAXLEN ~ STREAM_DLQ_MAXLEN`, default 10000) recebe `{original_id, reason, payload, ts}`, seguido de `XACK`+`XDEL` no stream principal. Métrica `diode_reconciler_dlq_total{reason}`.
- **Poison**: substituir as type assertions de `:224`/`:250` por `v, ok := ...`. Formato inválido, erro de Brotli ou erro de `proto.Unmarshal` passam a ser **erro permanente**: vão para a DLQ, recebem ACK+DEL e o loop continua (`:195-209` não retorna mais erro por mensagem).
- **Transitório vs permanente** em `CreateIngestionLogs`: erros do Postgres de conexão ou timeout (`pgconn.Timeout`, `net.Error`, classes SQLSTATE `08*`, `57P*`, `40001`) são **transitórios**. Nesse caso **não fazer ACK**: a mensagem fica no PEL e o reclaim a reprocessa. Os demais erros são permanentes e vão para a DLQ.
- Corrigir o nil-deref em `:178-179` (`if err != nil && strings.Contains(...)`).
- Atualizar o comentário de `:270-278` explicando a nova regra.

**FORA:**
- Ferramenta de replay da DLQ (um script `diode-dlq-replay` pode vir depois; por ora documentar `XRANGE` + `XADD` manual no runbook, SPEC-20261113-2153).
- Paralelizar o consumidor (D-21, SPEC-20261113-2151).

## Invariantes
- SEMPRE preservar a garantia da #591: nenhuma entrada permanece no stream principal indefinidamente. Toda mensagem termina em ACK+DEL, seja por sucesso, por ir para a DLQ ou por estourar `STREAM_MAX_DELIVERIES`.
- NUNCA fazer ACK de uma mensagem cuja persistência falhou por erro transitório.
- NUNCA derrubar o processo por causa do conteúdo de uma mensagem.

## Implementação

- `reconciler/ingestion_processor.go`: novo `classifyErr(err) (transient bool)`. `handleStreamMessage` passa a retornar `(ack bool, dlqReason string, err error)`. O loop decide entre ACK+DEL, DLQ+ACK+DEL ou deixar no PEL.
- Interface `RedisClient` (`:54-55`): adicionar `XAutoClaim`, `XPendingExt` e `XAdd` (mocks via mockery: `make generate` ou o alvo equivalente de mocks).
- O backpressure (`reconciler/backpressure.go`) continua usando `XLEN`. Com reclaim e DLQ o XLEN volta a drenar, então não muda.
- Testes com miniredis: mensagem sem `request`, com `request` não-string, Brotli inválido, proto inválido, `BulkCreate` com erro transitório (não ACK; reprocessa via reclaim) e consumidor morto (mensagens reclamadas).

### Modelo de dados
- Stream novo `diode.v1.ingest-stream.dlq` no Redis (DB do stream). Sem mudança no Postgres.

<!-- Alternativas: (a) voltar a não ACKar em erro (pré-#591) — rejeitada: reintroduz o latch do backpressure; (b) consumidor com nome fixo por StatefulSet ordinal — rejeitada: resolve só parte do caso 1 e exige trocar Deployment por StatefulSet; (c) gravar a falha em tabela Postgres em vez de DLQ Redis — rejeitada: o Postgres indisponível é justamente um dos casos. -->

## Riscos
- O reclaim pode reprocessar uma mensagem que um consumidor vivo ainda está processando (min-idle curto demais). Mitigação: default de 5 min, bem acima da latência de um lote. A dedup por `entity_hash` absorve duplicatas.
- A DLQ cresce sem dono. Mitigação: `MAXLEN ~`, métrica e alerta, e procedimento no runbook.

## Sinais de sucesso
- `XPENDING diode.v1.ingest-stream diode-reconciler` tende a 0 em regime, mesmo após restarts. Uma mensagem corrompida injetada aparece na DLQ e o reconciler segue de pé.

## Critério de aceite
- [ ] Nenhuma type assertion sem `ok` em `msg.Values` | verify: `cd diode-server && ! grep -nF '].(string))' reconciler/ingestion_processor.go`
- [ ] Testes de poison, transitório, reclaim e DLQ verdes | verify: `cd diode-server && go test -race -run 'Poison|Transient|Reclaim|DLQ' ./reconciler/...`
- [ ] Suíte completa do reconciler verde | verify: `cd diode-server && go test -race ./reconciler/...`
- [ ] Postgres derrubado por 1 min durante ingestão, nenhuma ingestão perdida após a volta (contagem de `ingestion_logs` = entidades enviadas) | evidence: manual @aliothbl
