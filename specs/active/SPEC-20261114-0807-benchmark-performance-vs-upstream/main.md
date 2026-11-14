# SPEC-20261114-0807: Benchmark de performance — fork × upstream (stack completa)

**Status:** active
**Porte:** G
**Owner:** @aliothbl
**Criada:** 2026-11-14 08:07
**Ativada:** 2026-11-14 08:07
**Pausada em:** —
**Concluída:** —
**Commit final:** —
**Keywords:** performance, benchmark, throughput, latência, pprof, benchstat, reconciler, plugin, rate-limiter, baseline
**Features:** reconciler, ingester, observability
**Branch:** `perf/bench-vs-upstream` (diode e diode-netbox-plugin, **só local, sem push**)
**Programa:** —
**Relacionadas:** `diode-netbox-plugin` (mesmo branch); SPECs de robustez do ROADMAP ficam para depois (decisão do owner, 2026-11-14).
**Origem:** pedido do owner em 2026-11-14: "rodar nosso código e o código da NetBox Labs e fazer uma comparação", com binários gerados e tudo documentado.
**Resumo:** Montar um benchmark reprodutível de ponta a ponta (SDK → ingester → reconciler → plugin → NetBox) e microbenchmarks Go, medir o baseline upstream, aplicar melhorias de performance no fork guiadas por profiling, gerar os binários e publicar a comparação com os problemas encontrados.

## Objetivo

Hoje não existe número algum sobre o throughput do Diode. O upstream não tem benchmark de ponta a ponta e só um pacote Go (`matching`) tem `Benchmark*`. Sem baseline não dá para afirmar que uma mudança melhora nada. O objetivo é (1) ter uma medição reprodutível, (2) achar os gargalos reais com profiling, (3) melhorar o fork onde o ganho for comprovado e (4) entregar binários e um relatório comparativo.

## Escopo

**DENTRO:**
- Harness de benchmark de ponta a ponta em docker, idêntico para as duas versões. Só muda o commit de `diode-server` e do plugin.
  - Baseline: `netboxlabs/diode@519ccec` + `netboxlabs/diode-netbox-plugin@8c3a3bb`.
  - Candidato: `perf/bench-vs-upstream` dos dois repos.
- Gerador de carga com o SDK Python (`diode-sdk-python`), com cenários fixos (criação inicial, reingestão idêntica/dedup, atualização parcial) e semente fixa.
- Métricas: entidades/s de ponta a ponta (ingest → aplicado no NetBox), latência p50/p95/p99 por entidade, CPU/RSS por serviço, chamadas HTTP reconciler→plugin, consultas SQL por entidade no plugin.
- Microbenchmarks Go (`go test -bench`, comparados com `benchstat`) nos caminhos quentes que o profiling apontar.
- Melhorias de performance no fork (Go e/ou plugin), cada uma com teste e medição antes/depois.
- Binários `ingester`, `reconciler` e `auth` das duas versões (linux/amd64), com checksums.
- Relatório comparativo: metodologia, ambiente, resultados, problemas encontrados.

**FORA:**
- Correções de robustez do diagnóstico (2141, 2143, 2142…). Se aparecerem durante o trabalho, entram só como "problema encontrado" no relatório.
- Push, PR para upstream e publicação de imagens.
- Tuning de infraestrutura (Postgres/Redis) que não faça parte do repositório. Configuração do stack é igual nas duas versões e documentada.

## Invariantes
- SEMPRE a mesma configuração de stack, hardware, dataset e semente nas duas versões. A única variável é o código.
- NUNCA declarar ganho sem ≥3 execuções por versão e dispersão reportada.
- NUNCA mudar comportamento observável (resultado no NetBox) por performance: o estado final do NetBox após cada cenário tem que ser idêntico entre baseline e candidato.

## Critério de aceite
- [x] Harness roda os dois lados com um comando e grava resultados em JSON | verify: `bench/run.sh --version baseline --scenario smoke` — 2026-11-14 15:10, executado para baseline e candidato
- [ ] Baseline medido (≥3 execuções por cenário) e registrado | evidence: manual @aliothbl
- [ ] Cada melhoria tem teste e medição antes/depois no journal | evidence: manual @aliothbl
- [x] Suítes de teste verdes no candidato (`go test ./...`; plugin v4.7.0) | verify: `cd diode-server && go test ./...` — 2026-11-14 15:10 @ `14ba79f` (plugin sem mudança de código; 1006 testes verdes no sync)
- [ ] Estado final do NetBox idêntico entre versões em todos os cenários | verify: `bench/.venv/bin/python bench/compare.py --state bench/results/*.json` — **não atendível como escrito:** o upstream não converge (duplica IPs, perde updates). Atendido entre todas as execuções do candidato (c=1 e c=4), e o conteúdo do upstream sem as duplicatas é igual ao do candidato. Reformular exige autorização do owner.
- [ ] Binários das duas versões com SHA256 | evidence: manual @aliothbl
- [ ] Relatório comparativo entregue | evidence: manual @aliothbl
