# SPEC-20261113-2141: Desligamento gracioso com SIGTERM em todos os serviços Go

**Status:** draft
**Porte:** P
**Owner:** @aliothbl
**Criada:** 2026-11-13 21:41
**Ativada:** —
**Pausada em:** —
**Concluída:** —
**Commit final:** —
**Keywords:** sigterm, sigkill, oklog-run, graceful-shutdown, kubernetes, apply-in-flight, redis-pel
**Features:** reconciler, ingester
**Branch:** _(a definir na ativação)_
**Depende de:** SPEC-20261113-2130-sync-upstream-develop-2026-11
**Programa:** —
**Impacto:** alto · **Complexidade:** baixa
**Estratégia upstream:** upstream-first — abrir PR em netboxlabs/diode; carregar no fork só até o merge; ao sincronizar, descartar o commit local se o upstream absorveu
**Origem:** diagnóstico 2026-11-13 (specs/DIAGNOSTICO-2026-11-13.md, achado D-1)
**Resumo:** O handler de sinal registra `os.Interrupt, os.Kill`. SIGKILL não pode ser capturado e SIGTERM (o sinal que Kubernetes e `docker stop` enviam) não está na lista, então todo deploy mata o processo sem `Stop()`. Trocar por `SIGTERM` + `SIGINT` e ordenar o desligamento dos componentes.

## Objetivo

Hoje, todo rolling update no Kubernetes encerra os três serviços sem passar por `Stop()`. O gRPC não faz `GracefulStop`, o lote do `AutoApplyProcessor` fica em APPLYING e as mensagens já lidas do stream ficam no PEL. Isso alimenta os achados D-3 (PEL órfão) e D-6 (reset global de APPLYING). Corrigir o sinal é a menor mudança com maior efeito sobre a robustez operacional.

## Escopo

**DENTRO:**
- `diode-server/server/server.go:118`: `run.SignalHandler(s.ctx, os.Interrupt, os.Kill)` → `run.SignalHandler(s.ctx, os.Interrupt, syscall.SIGTERM)`.
- Ordem de parada no reconciler. `IngestionProcessor.Stop()` (`reconciler/ingestion_processor.go:144-156`) cancela o ctx e **fecha os clientes Redis na hora**, enquanto `handleStreamMessage` pode estar entre o `BulkCreateIngestionLogs` e o `XAck` (`:279`). Esperar o fim da mensagem em curso (WaitGroup ou canal `done` sinalizado pelo loop) antes de fechar os clientes.
- `AutoApplyProcessor.Stop()` (`reconciler/auto_apply_processor.go:113`) e `IngestionLogProcessor.Stop()` (`reconciler/ingestion_log_processor.go:156`): o mesmo padrão. Terminar o lote em curso, com um teto `SHUTDOWN_GRACE_SECONDS` (default 25 s, abaixo dos 30 s padrão de `terminationGracePeriodSeconds`).
- Confirmar que `ingester/component.go:126-130` (`GracefulStop` antes de `Close`) e `reconciler/server.go:91-93` continuam corretos.
- Teste unitário em `server/` que envia SIGTERM ao próprio processo e verifica que todos os `Stop()` foram chamados.

**FORA:**
- Lease de claim (SPEC-20261113-2144).
- `XAUTOCLAIM` do PEL (SPEC-20261113-2143).
- `terminationGracePeriodSeconds`/`preStop` no chart (SPEC-20261113-2148).

## Invariantes
- SEMPRE terminar ou devolver à fila o trabalho em curso antes de fechar conexões de Redis e Postgres.
- NUNCA bloquear o shutdown sem limite: estourou o teto, logar o que ficou pendente e sair.

## Implementação

1. `server/server.go`: importar `syscall` e trocar os sinais. O `oklog/run` já chama o `interrupt` de cada actor quando um deles retorna. Não há mudança de estrutura.
2. Componentes do reconciler: trocar o `cancel()` seguido de `Close()` imediato por cancelar, depois `select { case <-p.done: ; case <-time.After(grace): log }`, e só então fechar. O loop de consumo (`consumeIngestionStream`, `:158-211`) fecha `p.done` ao sair.
3. Config: `SHUTDOWN_GRACE_SECONDS` em `reconciler/config.go` e `ingester/config.go` (envconfig, default 25).

### Modelo de dados
—

<!-- Alternativas: (a) só trocar o sinal, sem ordenar o Stop — rejeitada: ainda fecha o Redis no meio do XAck e produz exatamente a entrada órfã que a SPEC quer evitar; (b) signal.NotifyContext no main e cancelar o ctx raiz — rejeitada: duplica o mecanismo do oklog/run que o upstream já usa. -->

## Riscos
- Um lote de apply lento (NetBox sob carga) pode passar do teto. Mitigação: o teto é configurável, e o que sobrar em APPLYING continua coberto pelo reset (D-6) até a SPEC de lease.
- O teste de sinal é sensível a flakiness em CI. Mitigação: usar `syscall.Kill(os.Getpid(), SIGTERM)` com timeout curto e canal de confirmação.

## Sinais de sucesso
- Num rolling update do reconciler, a contagem de linhas em APPLYING após o restart é 0 e o `XPENDING` do grupo não cresce.

## Critério de aceite
- [ ] `server.go` trata SIGTERM e SIGINT; nenhuma referência a `os.Kill` em `SignalHandler` ← specctl check
- [ ] O Stop dos 3 componentes do reconciler espera a unidade de trabalho em curso (com teto) antes de fechar clientes ← specctl check
- [ ] Teste de SIGTERM em `server/` verde em `go test -race ./server/...` ← specctl check
- [ ] Rolling update manual em kind/minikube sem APPLYING residual | evidence: manual @aliothbl
