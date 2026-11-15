# Plano noturno — 2026-11-14 23:16 (autônomo, via /loop)

> Este arquivo é a fonte da verdade do loop. A cada rodada: leia este arquivo, faça o próximo passo não concluído, atualize o checklist e o LOG no fim. Ele fica em `bench/out/` (fora do git).

## Contexto (a sessão pode não ter o histórico)
- **Repos de trabalho** (`/home/alatour/repositories/netbox/`), todos forks `aliothbl/*` com `origin` = fork e `upstream` = netboxlabs:
  - `diode`, branch `perf/bench-vs-upstream`, com o harness em `bench/` (ver `bench/README.md`);
  - `diode-netbox-plugin`, branch `perf/bench-vs-upstream` (= `develop`);
  - `netbox-branching`, branch `spec/20261114-1736-benchmark-reproducao`.
  - Existem clones antigos em `/home/alatour/repositories/netboxlabs/`: **não usar**.
- **SPECs ativas** (formato SDD em `specs/`; leia `specs/README.md` de cada repo):
  - diode `specs/active/SPEC-20261114-0807-benchmark-performance-vs-upstream/` (`journal.md`, `report.md`);
  - netbox-branching `specs/active/SPEC-20261114-1736-benchmark-reproducao-branching/` (`journal.md`), mais `specs/DIAGNOSTICO-2026-11-14.md` (achados F1–F15).
- **Harness:** `bench/run.sh --version candidate --scenario <std|smoke|large|branch-merge-race|branch-double-merge> [--runs N] [--keep]`.
  - Knobs por variável de ambiente: `CONCURRENCY`, `BATCH_SIZE`, `BRANCHING_SRC`, `BENCH_COMPOSE_EXTRA` (overlays `bench/compose.*.yaml`).
  - Resultados em `bench/results/*.json` (`error_summary`, `diode_pg_deadlocks`, `reconciler_errors`, `netbox_state`) e `*.rows.json.gz`.
  - Comparação: `bench/.venv/bin/python bench/compare.py --state bench/results/*.json`.
  - Cenários de branching ficam em `bench/branch_scenarios.py`.
  - Cada `std` com c=4 leva ~5 min. Rode em background e espere a notificação; nunca rode dois stacks ao mesmo tempo (mesmo nome de projeto e portas).

## Regras (inegociáveis)
1. **Commit e push só para `origin` (forks aliothbl).** Nunca `upstream`, nunca `--force`, nunca PR (`gh pr`), nunca apagar branch remoto. `develop` só recebe cherry-pick de commits de código já testados, pelo worktree temporário (`git worktree add <scratch> develop`).
2. **Timestamps sempre de `date '+%Y-%m-%d %H:%M'`, em chamada ANTERIOR à edição**, nunca estimados. LOG dos journals é append-only: erro se corrige com entrada nova.
3. Toda mudança de código: teste unitário + `cd diode-server && go test ./...` verde (plugin: suíte docker v4.7.0) antes do commit. Commits com trailer `Spec: SPEC-...` e `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
4. Nada de "ganho" ou "corrigido" sem medição antes/depois (≥3 execuções) e evidência no journal. Resultado inconclusivo é registrado como inconclusivo.
5. Não apagar nada fora de `bench/out/`, `bench/results/_harness-dev/` e worktrees temporários. Nada de `docker system/volume/image prune`.
6. **Parar e anotar** (seção "Bloqueios", e terminar o loop) se: precisar de decisão do owner, um teste existente quebrar sem causa clara, ou algo exigir ação fora destas regras.

## Checklist (em ordem)
- [x] **1. P-9: correção definitiva do deadlock no reconciler** (diode). → Causa real no plugin (M-6 `ae04547`); M-5 `cff6f70`; ver LOG 08:49.
  - Contexto: journal 0807, entradas P-9 e M-4 (`91ecf9d`, retry em 40P01). Ainda houve 1 lote de 25 FAILED no estresse (`CONCURRENCY=8 BATCH_SIZE=25`), com vítima não identificada.
  - [x] 1a. Reproduzir com captura de `error_summary` (estresse ×N até ocorrer, máx. 8 execuções) e identificar a operação vítima.
  - [x] 1b. (descartado: não é a causa; ver LOG 06:43) Corrigir a causa raiz. Direção: contadores `ingestion_log_state_object_type_counts` sem hot rows, com tabela de deltas append-only (INSERT sem conflito) + consolidação periódica; leitura = soma da base + deltas. Migração goose nova (Up/Down); manter a API de leitura (`CountIngestionLogsPerState`/list totals) idêntica.
  - [x] 1c. Testes (unitários + consistência dos totais) e estresse ×5: meta 0 deadlocks e 0 FAILED. Medir throughput `std` c=4 ×3 antes/depois (não pode piorar >3%).
  - [x] 1d. Commit no perf branch, cherry-pick no develop, push origin; journal + report atualizados.
- [ ] **2. netbox-branching F6: Diode ingerindo no branch padrão durante um sync.**
  - Cenário novo `branch-sync-ingest` em `branch_scenarios.py`: branch padrão, carga contínua no branch e, no meio, `branch.sync(user)` (shell pré-aquecido, gatilho `/tmp/bench-sync-go`, como no merge-race).
  - Para haver o que sincronizar, gravar no main antes do sync (ex.: objetos via nbshell).
  - Medir: entidades FAILED com 400 durante a janela, duração do sync, estado final. ≥5 execuções.
  - Atualizar DIAGNOSTICO (linha F6) e journal 1736.
- [ ] **3. netbox-branching F7: custo do roteador por query** (o A/B de ponta a ponta foi inconclusivo).
  - Medir direto: microbenchmark em nbshell, com N chamadas a `BranchAwareRouter.db_for_write/db_for_read` e `supports_branching` para os modelos que o Diode toca, sem branch ativo (µs/chamada); mais py-spy durante carga `std` com o plugin, medindo a % de amostras em `netbox_branching/database.py` e `utilities.py:supports_branching`.
  - Estimar o custo por entidade e registrar.
- [ ] **4. netbox-branching F3: sync pula mudança do main commitada tarde.**
  - Cenário `branch-sync-watermark`: transação longa no main aberta antes do sync (nbshell com `transaction.atomic()` + sleep, gatilhos por arquivo), commitada depois da leitura do sync; segundo sync; comparar o objeto no branch × main. ≥5 execuções.
- [ ] **5. Fechamento:** resumo da noite no topo deste arquivo (o que foi feito, números, o que ficou), journals atualizados, tudo commitado e enviado ao origin. Encerrar o loop.

## Entregáveis finais — NÃO fazer neste loop (pedido do owner, 2026-11-15)
Só depois que **toda** a implementação estiver concluída e verificada, e com o OK do owner:
1. Documentação técnica completa **em inglês** no fork: visão geral, repositórios com links, mudanças por repositório (arquivos e decisões), justificativa, benchmarks com metodologia e comparação com o upstream, validação e limitações, compatibilidade com o upstream. Nada inventado; tudo rastreável aos resultados e commits.
2. Rascunho de e-mail em inglês para a engenharia da NetBox Labs pedindo revisão técnica, com link para o fork e a doc. Deixar claro que PRs upstream só acontecem se o time achar adequado. **Não enviar**: só rascunho para o owner.
3. PRs upstream só depois do feedback da NetBox Labs e de um novo OK explícito do owner.

O item 5 deste loop **não** inclui esses entregáveis. Ele só deixa anotado o que ainda falta implementar antes deles (ex.: P-10, correções F1/F2/F6 no netbox-branching).

## Bloqueios
—

## LOG
- 2026-11-15 08:51 — **Item 1 concluído** (`c4151bd`: journal e report da 0807 atualizados; plugin develop = `ae04547`; diode develop `93aa636`; tudo no origin). A/B do plugin inconclusivo (ruído ~9%). **Item 2 iniciado:** cenário `branch-sync-ingest` em `branch_scenarios.py`: 400 tags no main com change logging, branch padrão do Diode, sync em 30% da carga. Execução de teste rodando.
- 2026-11-15 08:18 — **Validação com o plugin `ae04547`** (estresse ×5, `072104`…`074112`):
  - 0 respostas 500 de hierarquia. Em `073622`, o plugin deu 1 retry de hierarquia e o lote passou. Em `073154`, 1 deadlock no claim foi absorvido pelo M-4.
  - **Achado novo (P-10 candidato):** em `072104`, 1 lote estourou o timeout de 120 s (26 linhas de timeout, 25 FAILED). Foi a 1ª execução após recriar o seed, com 0 retries do plugin, portanto sem relação com a correção. Causa ainda não identificada.
  - Throughput `std` c=4: 23,42 ± 3,25 (n=3, `074601`) contra 25,93 ± 2,27 (n=5, `20261114-214919`), −9,7%, t≈1,2. **Inconclusivo**: dias e seeds diferentes. Rodando um A/B intercalado ed63aff×ae04547 com 3 pares (`bench/out/ab-plugin.sh`).
  - diode develop recebeu o cherry-pick `93aa636` (fix do truncate; `go test` verde no worktree); push no origin.
- 2026-11-15 07:13 — Plugin `ae04547` (perf branch): retry do ValidationError de hierarquia e erro por entidade; suíte v4.7 com 1015 testes OK; ruff limpo (o ruff da imagem não existe, rodei pelo `bench/.venv`). Push para o origin dos perf branches: diode `fd74bac`, plugin `ae04547`. Em andamento: estresse ×5 (sem parar no erro) + `std+nobranching` c=4 ×3 para throughput (referência "antes": `20261114-214919` ×5, plugin `ed63aff`).
- 2026-11-15 06:43 — **P-9: vítima identificada.** No estresse de sanidade, a run 2 (`20261115-062938`) teve 25 FAILED: 25× `plan failed in bulk-plan-apply … ERR_OPS_APPLY_CHANGE_SET {"request":{"__all__":["internal error"]}}`, com 0 deadlocks no Postgres do Diode. Do lado do NetBox: `ValidationError: 'The hierarchy was modified concurrently; please retry.'`, vindo de `netbox/models/ltree.py:445` (NetBox 4.7). Os triggers ltree usam advisory locks por árvore; quando perdem um 40P01, ele vira ValidationError (`from None`). O retry do plugin só reconhece `OperationalError` 40P01, então o erro sobe até o handler genérico e devolve 500 para o lote inteiro. **A causa raiz está no plugin, não nos contadores do Diode:** o 1b (deltas) fica descartado como correção do P-9. Commits no diode: `cff6f70` (fix do truncate) e `fd74bac` (captura de erros do NetBox + resultados). Correção no plugin: retry também nesse ValidationError e, esgotadas as tentativas, erro por entidade em vez de 500. 9 testes novos OK; suíte v4.7 completa rodando.
- 2026-11-15 06:25 — **1a encerrado sem reprodução:** 8/8 execuções de estresse limpas (`20261115-044342`…`051612`: rec_err 0, pg_deadlocks 0, FAILED 0). Desde o M-4, foram 16 execuções de estresse, com deadlock em 2 e FAILED em 1 (`20261114-222007` run1: 25 FAILED na fase initial, 25 ERROR, 1 deadlock; sem texto do erro, pois o `error_summary` ainda não existia). **Análise por código** (vítima não confirmada):
  - (A) Lote inteiro FAILED vindo do plugin (erro HTTP ou plan): bate com exatamente 25 ERROR e só na fase initial.
  - (B) **Defeito latente real, encontrado e corrigido:** em `BulkPersistChangeSets`, o truncate de retenção roda DEPOIS do commit, mas a falha dele era devolvida como falha do persist. No bulk-plan-apply, isso marca FAILED um lote já APPLIED. No plan-only, persiste os change sets duas vezes (fallback por item). O caminho por item já ignorava esse erro. Correção: `persistThenTruncate` registra o erro em warning e devolve os resultados commitados; 3 testes; `go test ./...` verde. Não é o "corrigido" do P-9: (B) daria 26 ERROR, não 25.
  - `run.sh` agora também grava os erros do NetBox no `error_summary`, para identificar (A) na próxima ocorrência.
  - **1b (contadores com deltas) adiado, decisão para o owner de manhã:** com o M-4, os deadlocks restantes são raros (2/16) e o retry absorveu os que vieram; não há medição que permita provar ganho (Regra 4) com ≥3 execuções. Fica registrado como opção.
  - Em andamento: estresse ×3 de sanidade com a correção (B).
- 2026-11-15 04:43 — Sessão reiniciada; loop retomado. 1a recomeçado do zero: `bench/out/stress.sh 8` (std+nobranching, c=8, batch 25, para na 1ª execução com rec_err/deadlock/FAILED/error_summary). Log da execução corrente em `bench/out/stress-last.log`. Arquivo `.load` órfão da execução interrompida movido para `results/_harness-dev/`.
- 2026-11-14 23:21 — Loop interrompido para reiniciar a sessão (permissões novas + modo automático). Estresse do 1a parado no meio da 1ª execução; stack derrubado (0 containers). **Na nova sessão, recomeçar o 1a do zero.** O desenho do 1b abaixo continua válido.
- 2026-11-14 23:19 — Item 1a iniciado: estresse `std+nobranching`, c=8, batch 25, até 8 execuções, parando na primeira com erro no reconciler (task em background). Achado para o 1b: `ingestion_log_state_object_type_counts` (upstream #535/#540) **não é lido por nenhuma query do servidor**, só mantido pelos triggers; `CountIngestionLogsPerState` faz COUNT(*) direto. Desenho do 1b: preservar a leitura para eventuais consumidores externos, com uma VIEW de mesmo nome sobre base + deltas append-only, e mover a escrita dos triggers para INSERT de deltas (sem ON CONFLICT, sem hot rows).
- 2026-11-14 23:16 — Plano criado. Estado: diode `c3565d4` (perf) / develop `d9dd64f`; plugin `ed63aff`; netbox-branching `5810e84`.
