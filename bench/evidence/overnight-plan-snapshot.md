# Plano noturno — 2026-11-14 23:16 (autônomo, via /loop)

## RESUMO DA NOITE (2026-11-15 11:56)

**Itens 1–5 concluídos.** Tudo commitado e enviado **só para os forks** (`origin`). Nada foi para a NetBox Labs.

### Correções (código, com testes)
| ID | Repo | Commit | O quê | Validação |
|---|---|---|---|---|
| M-6 (causa do P-9) | diode-netbox-plugin | `ae04547` (perf = develop) | Deadlock de árvore (ltree) do NetBox 4.7 vinha como ValidationError; o retry não o reconhecia e o lote inteiro recebia 500 | 9 testes; suíte v4.7 (1015) verde; estresse ×5 sem 500 de árvore, 1 retry salvou um lote |
| M-5 | diode | `cff6f70` (develop `93aa636`) | Falha do truncate pós-commit virava falha do persist: lote APPLIED marcado FAILED, ou change sets gravados 2× | 3 testes; `go test` verde |
| M-7 (P-11) | diode | `9e453e1` (develop `1c5b5dd`) | Corpo de erro não-JSON (400 do netbox-branching) deixava o lote preso em APPLYING sem erro | 5 testes; `go test` verde; `branch-sync-ingest` ×5 |

### Reproduções do netbox-branching (SPEC-1736; DIAGNOSTICO atualizado)
- **F6 — 5/5:** num sync de ~38 s, 190–265 de 540 entidades do Diode falham com `400 Selected branch is not ready.`, e 21–29 devices (com suas interfaces e IPs) nunca chegam ao branch.
- **F3 — 5/5:** uma mudança do main salva antes de um sync e commitada depois dele nunca chega ao branch; o sync seguinte não a enxerga.
- **F7 — medido:** `supports_branching` 2,38 µs/chamada (0,08 µs com cache); o router fica com 1,36% do request do Diode. Ganho de um cache ≤ ~1%.
- (Já de 14/11: F2 6/6, F1 9/9.)

### Medições inconclusivas (registradas como tal)
- Throughput com M-6: A/B intercalado (3 pares), −1,7 ± 3,8 ent/s por par. O ruído (~9%) é maior que a meta de 3%; no caminho sem erro, a mudança não executa código novo.

### Em aberto: falta implementar antes da documentação final
1. **P-10** (diode/NetBox): lote ocasional passa dos 120 s de apply sem erro no NetBox (1/26 execuções de estresse; 2/5 durante sync). A amostragem de `pg_stat_activity` já está no harness, falta uma ocorrência com ela ligada.
2. **Correções no netbox-branching** (até agora só reproduzidas):
   - **F6:** 503 + Retry-After nos estados transitórios. O Diode já faz retry em 503.
   - **F3:** marca d'água por sequência em vez de `time`.
   - **F2:** escritas em voo durante o merge.
   - **F1:** compare-and-set nas transições de status.
   - **F7:** cache em `supports_branching` (ganho pequeno).
3. Upstream-first: M-1, M-2, M-4, M-5, M-7 (diode) e M-6 (plugin) são candidatos a PR, **só depois do feedback da NetBox Labs** (ver "Entregáveis finais").
4. Opção descartada: contadores com deltas (1b). Não era a causa do P-9.

### Evidências
`aliothbl/diode` branch `perf/bench-vs-upstream`:
- `bench/results/` (JSON + estado + logs completos por execução, a partir de 15/11 ~09:10);
- `bench/evidence/` (saídas de tasks e testes, profiles, F7, snapshot deste plano).

Journals: diode SPEC-0807 (`journal.md`, `report.md`), netbox-branching SPEC-1736 (`journal.md`, `DIAGNOSTICO-2026-11-14.md`).


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
7. **Guardar todas as saídas** (pedido do owner, 2026-11-15, para a documentação final): todo resultado de benchmark, saída de task em background e saída de suíte de testes (`go test`, suíte do plugin) é copiado para `bench/evidence/` (com uma linha no índice `bench/evidence/README.md`) e commitado. Os scripts auxiliares ficam em `bench/tools/` (não mais em `bench/out/`). Nada de saída só em `/tmp` ou em `bench/out/`.
6. **Parar e anotar** (seção "Bloqueios", e terminar o loop) se: precisar de decisão do owner, um teste existente quebrar sem causa clara, ou algo exigir ação fora destas regras.

## Checklist (em ordem)
- [x] **1. P-9: correção definitiva do deadlock no reconciler** (diode). → Causa real no plugin (M-6 `ae04547`); M-5 `cff6f70`; ver LOG 08:49.
  - Contexto: journal 0807, entradas P-9 e M-4 (`91ecf9d`, retry em 40P01). Ainda houve 1 lote de 25 FAILED no estresse (`CONCURRENCY=8 BATCH_SIZE=25`), com vítima não identificada.
  - [x] 1a. Reproduzir com captura de `error_summary` (estresse ×N até ocorrer, máx. 8 execuções) e identificar a operação vítima.
  - [x] 1b. (descartado: não é a causa; ver LOG 06:43) Corrigir a causa raiz. Direção: contadores `ingestion_log_state_object_type_counts` sem hot rows, com tabela de deltas append-only (INSERT sem conflito) + consolidação periódica; leitura = soma da base + deltas. Migração goose nova (Up/Down); manter a API de leitura (`CountIngestionLogsPerState`/list totals) idêntica.
  - [x] 1c. Testes (unitários + consistência dos totais) e estresse ×5: meta 0 deadlocks e 0 FAILED. Medir throughput `std` c=4 ×3 antes/depois (não pode piorar >3%).
  - [x] 1d. Commit no perf branch, cherry-pick no develop, push origin; journal + report atualizados.
- [x] **2. netbox-branching F6: Diode ingerindo no branch padrão durante um sync.**
  - Cenário novo `branch-sync-ingest` em `branch_scenarios.py`: branch padrão, carga contínua no branch e, no meio, `branch.sync(user)` (shell pré-aquecido, gatilho `/tmp/bench-sync-go`, como no merge-race).
  - Para haver o que sincronizar, gravar no main antes do sync (ex.: objetos via nbshell).
  - Medir: entidades FAILED com 400 durante a janela, duração do sync, estado final. ≥5 execuções.
  - Atualizar DIAGNOSTICO (linha F6) e journal 1736.
- [x] **3. netbox-branching F7: custo do roteador por query** (o A/B de ponta a ponta foi inconclusivo).
  - Medir direto: microbenchmark em nbshell, com N chamadas a `BranchAwareRouter.db_for_write/db_for_read` e `supports_branching` para os modelos que o Diode toca, sem branch ativo (µs/chamada); mais py-spy durante carga `std` com o plugin, medindo a % de amostras em `netbox_branching/database.py` e `utilities.py:supports_branching`.
  - Estimar o custo por entidade e registrar.
- [x] **4. netbox-branching F3: sync pula mudança do main commitada tarde.**
  - Cenário `branch-sync-watermark`: transação longa no main aberta antes do sync (nbshell com `transaction.atomic()` + sleep, gatilhos por arquivo), commitada depois da leitura do sync; segundo sync; comparar o objeto no branch × main. ≥5 execuções.
- [x] **5. Fechamento:** resumo da noite no topo deste arquivo (o que foi feito, números, o que ficou), journals atualizados, tudo commitado e enviado ao origin. Encerrar o loop.

## Entregáveis finais — NÃO fazer neste loop (pedido do owner, 2026-11-15)
Só depois que **toda** a implementação estiver concluída e verificada, e com o OK do owner:
1. Documentação técnica completa **em inglês** no fork: visão geral, repositórios com links, mudanças por repositório (arquivos e decisões), justificativa, benchmarks com metodologia e comparação com o upstream, validação e limitações, compatibilidade com o upstream. Nada inventado; tudo rastreável aos resultados e commits.
2. Rascunho de e-mail em inglês para a engenharia da NetBox Labs pedindo revisão técnica, com link para o fork e a doc. Deixar claro que PRs upstream só acontecem se o time achar adequado. **Não enviar**: só rascunho para o owner.
3. PRs upstream só depois do feedback da NetBox Labs e de um novo OK explícito do owner.

O item 5 deste loop **não** inclui esses entregáveis. Ele só deixa anotado o que ainda falta implementar antes deles (ex.: P-10, correções F1/F2/F6 no netbox-branching).

## Bloqueios
—

## LOG
- 2026-11-15 11:56 — **Item 4 concluído: F3 reproduzido 5/5** (`20261115-111927`; branching `1d240c8`; diode `6fe3831`). **Item 5:** M-7 levado ao develop do diode por cherry-pick (`1c5b5dd`, `go test` verde); journal e report da 0807 atualizados (P-11, M-7, P-10); resumo da noite no topo. Loop encerrado.
- 2026-11-15 11:11 — **Item 3 concluído (F7):** microbenchmark: `supports_branching` 2,38 µs/chamada (0,08 µs com cache por modelo); `db_for_read`/`db_for_write` 3,0–3,2 µs. py-spy (profile de 14/11, std c=1): router em 1,36% das amostras dentro do request, `supports_branching` em 1,04%. Ganho estimado de um cache: ≤ ~1% do request. Commits: branching `0efb53f`, diode `863857b`. A tentativa de py-spy novo falhou (sem pip na imagem); a ferramenta foi corrigida, mas não reexecutada. **Item 4 iniciado:** cenário `branch-sync-watermark` (escritor do main com a transação aberta antes do sync 1 e commitada depois; em seguida o sync 2). Execução de teste rodando.
- 2026-11-15 10:24 — **Item 2 concluído: F6 reproduzido 5/5** (`20261115-094726`): sync de 36,8–38,5 s; 190–265/540 FAILED com `400 Selected branch is not ready.`; 21–29 devices, 84–118 interfaces e 85–118 IPs nunca chegam ao branch. Em 2/5, 1–2 lotes com timeout de 120 s (P-10 de novo; o NetBox não registrou nada). `run.sh` agora amostra o `pg_stat_activity` do NetBox a cada 2 s para pegar a próxima ocorrência. DIAGNOSTICO F6 e journal 1736 atualizados (`f092788`); diode `3230bc2`. Correção proposta para o F6 (a implementar): middleware com 503 + Retry-After nos estados transitórios. **Item 3 iniciado** (F7: microbenchmark do router).
- 2026-11-15 09:47 — **F6 reproduzido** (teste `090842`, c=4, batch 25, 2000 mudanças no main): sync de 56,8 s; durante ele o NetBox respondeu `400 Selected branch is not ready.` a todo `bulk-plan-apply`, e **190 de 540 entidades (21 devices, 84 interfaces, 85 IPs) nunca chegaram ao branch**. Mesmo assim, o log de ingestão mostrou **0 FAILED**, por causa de um defeito novo, o **P-11** (Diode): o corpo 400 em texto puro ia para um `json.RawMessage`, o `json.Marshal` do erro falhava e `UpdateIngestionLogStateWithError` retornava antes de gravar, deixando as entidades presas em APPLYING sem erro até reiniciar o reconciler. Correção `9e453e1`: `NewError` guarda detalhes não-JSON como string JSON, e o repositório usa a mensagem como fallback; 5 testes; `go test ./...` verde (saída em `bench/evidence/logs`). Rodando `branch-sync-ingest` ×5 com a correção.
- 2026-11-15 09:08 — Item 2, execução de teste (`090314`, batch 100, concorrência padrão): sync de 15,5 s com 400 mudanças, 0 FAILED, nada faltando no branch. **Não exercitou o F6:** o sync caiu dentro de um único request em voo, que já tinha passado pelo middleware quando o branch estava READY; o lote seguinte só começou depois do sync. `run.sh` agora grava os logs completos dos containers (`<resultado>.logs/`). Commit `606aabc`. Rodando nova execução de teste com `CONCURRENCY=4 BATCH_SIZE=25 SYNC_OBJECTS=2000` (requests mais frequentes, sync mais longo).
- 2026-11-15 09:04 — Owner pediu para guardar todas as saídas para a documentação final (Regra 7). Em `a213619`: as 22 saídas de tasks desde 14/11 foram para `bench/evidence/` (com índice), junto com os logs de console e um snapshot deste plano; `stress.sh`/`ab-plugin.sh` foram para `bench/tools/`. **Pendente, logo após a execução de teste do sync:** `run.sh` passa a gravar os logs completos de todos os containers por execução (`<resultado>.logs/*.log.gz`). Não editar o `run.sh` com uma execução em andamento, porque o bash lê o script enquanto executa.
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
