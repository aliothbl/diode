# bench/ — benchmark de ponta a ponta do Diode (fork × upstream)

Diretório só do fork, como `specs/`: não existe no upstream e não gera conflito de sync.
Relatório e decisões: `specs/active/SPEC-20261114-0807-benchmark-performance-vs-upstream/`.

## Uso

```bash
bench/run.sh --version baseline  --scenario std --runs 3   # netboxlabs: diode 519ccec + plugin 8c3a3bb
bench/run.sh --version candidate --scenario std --runs 3   # este checkout + ../diode-netbox-plugin
bench/.venv/bin/python bench/compare.py --state bench/results/*.json
```

- Cenários: `smoke` (10 devices, 50 entidades), `std` (200 devices, 1800 entidades) e `large` (1000 devices × 8 ifaces, 17000 entidades).
- Knobs, iguais para os dois lados: `CONCURRENCY`, `BATCH_SIZE`, `RATE_RPS`, `RATE_BURST`, `PLUGIN_TIMEOUT`, `NETBOX_WORKERS`.
- `--keep` deixa o stack de pé depois da última execução, para inspeção. Portas: NetBox `127.0.0.1:58000`, Postgres do Diode `:55432`, Redis do Diode `:56379`, gRPC do ingester `:58081`, pprof do reconciler e do ingester `:56060` / `:56061`.
- Profiling (não usar para medir): `BENCH_COMPOSE_EXTRA=$PWD/bench/compose.profile.yaml bench/run.sh ...`.

## O que cada execução faz

1. Compila `ingester`, `reconciler` e `auth` da versão para `out/bin/<versão>-<commit>/`, com `SHA256SUMS`. Esses são os binários medidos.
2. Restaura o snapshot do banco do NetBox já migrado (`out/seed/<rev do plugin>/`). O snapshot é criado no primeiro boot de cada revisão do plugin, que leva cerca de 8 minutos.
3. Sobe o stack (`compose.yaml`) e roda `loadgen.py` nas fases `initial`, `reingest` e `update`. Enquanto isso, amostra `docker stats`.
4. Grava `results/<ts>-<versão>-<cenário>-runN.json` com métricas, curva e recursos, e `.rows.json.gz` com o estado final completo do NetBox, vindo de `netbox/dump_state.py`.

## Arquivos

| Arquivo | Papel |
|---|---|
| `compose.yaml` | Stack idêntico para as duas versões; só os binários e o código do plugin mudam |
| `auth_stub.py` | Substitui diode-auth/Hydra (token + introspect). Nunca expor fora do benchmark |
| `loadgen.py` | Carga via classes do SDK Python, direto no gRPC do ingester |
| `netbox/dump_state.py` | Impressão digital do estado do NetBox, independente de PK e de ordem |
| `compare.py` | Tabela por versão/cenário/knobs e verificação do estado final |
| `results/_harness-dev/` | Execuções feitas durante o desenvolvimento do harness. Não comparar |

`out/`, `.venv/` e `.baseline/` (worktrees do upstream) ficam fora do git.
