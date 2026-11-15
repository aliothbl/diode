# Relatório — Diode fork × NetBox Labs (benchmark de ponta a ponta)

**Data:** 2026-11-14 · **SPEC:** `SPEC-20261114-0807-benchmark-performance-vs-upstream` · **Branch:** `perf/bench-vs-upstream` (local, sem push)

| | Upstream (NetBox Labs) | Fork (candidato) |
|---|---|---|
| diode-server | `519ccec` (develop, 2026-11-05) | `14ba79f` = `519ccec` + M-1 + M-2 |
| diode-netbox-plugin | `8c3a3bb` (develop, 2026-10-27) | `d7a0cc4` = `8c3a3bb` + `specs/` (**sem mudança de código**) |

## 1. Resumo

O upstream, na configuração padrão, **perde e duplica dados** sob carga moderada. A causa raiz é um único defeito no reconciler: o `bulk-plan-apply` é reenviado depois de um timeout de 5s, enquanto o NetBox ainda aplica o primeiro request. As duas correções do fork eliminam isso. O fork é mais rápido **e** chega sempre ao mesmo estado correto no NetBox.

Carga `std`: 200 devices, 800 interfaces e 800 IPs, ou seja, 1800 entidades por fase. Média ± desvio-padrão; cada execução começa de banco vazio. Upstream com a configuração padrão: n=6, em duas rodadas de 3. Demais: n=3.

| Configuração | Métrica | Upstream | Fork | Diferença |
|---|---|---|---|---|
| Padrão (concorrência 1) | Criação (ent/s) | 8,66 ± 1,89 | **11,34 ± 0,20** | **+31%** |
| | Reingestão idêntica (ent/s) | 48,0 ± 8,8 | **50,5 ± 0,4** | +5% |
| | Atualização (ent/s) | 9,80 ± 1,74 | **10,52 ± 0,05** | +7% |
| | Timeouts de apply por execução | ~44 | 0 | — |
| | IPs no NetBox (esperado 800) | **1276 / 962 / 934 / 974 / 950 / 962** | 800 em todas | — |
| | CPU média do NetBox | 101–138% | 74% | −35% |
| Concorrência 4 | Criação (ent/s) | 15,98 ± 0,29 ¹ | **20,08 ± 0,17** | +26% ¹ |
| | Reingestão idêntica (ent/s) | 16,4 ± 0,2 ¹ | **84,7 ± 0,0** | **+415%** |
| | Timeouts de apply por execução | **~5500** | 0 | — |
| | Entidades registradas como FAILED | **100%** | 0% | — |
| | IPs no NetBox (esperado 800) | **1518–1608** | 800 | — |
| | Interfaces com update perdido | **136–189** | 0 | — |
| | CPU média do NetBox | 400% (saturado) | 236% | −41% |
| **Melhor configuração de cada lado** | Criação | 8,66 ent/s, **estado errado** | **20,08 ent/s, estado correto** | **2,3×** |

A dispersão também conta. No upstream, o tempo da fase de criação variou de 182s a 369s entre execuções idênticas. No fork, de 157s a 162s.

¹ No upstream com concorrência 4, "ent/s" mede a rapidez com que as entidades **terminam** (em FAILED), não a rapidez com que são aplicadas corretamente. O NetBox fica com o dobro de IPs e com atualizações faltando.

**Estado final:** as 6 execuções do fork (concorrência 1 e 4) terminam com o NetBox em estado **byte a byte idêntico**. Cada execução do upstream termina num estado diferente:
- Com concorrência 1, há de 150 a 174 IPs duplicados por execução (nas 3 execuções com dump completo).
- Com concorrência 4, há de 718 a 808 IPs duplicados, mais 136 a 189 interfaces com a atualização perdida.

Removidas as duplicatas e descontadas as atualizações perdidas, o conteúdo do upstream é exatamente o do fork.

## 2. Problemas encontrados

| ID | Severidade | Problema | Status no fork |
|---|---|---|---|
| **P-1** | **Crítica** | `bulk-plan-apply` reenviado após timeout → objetos duplicados, atualizações perdidas, log de ingestão falso | **Corrigido (M-1)** |
| **P-7** | Média | Lote reivindicado aplicado fora da ordem de ingestão → log credita a entidade errada; ordem varia entre execuções | **Corrigido (M-2)** |
| P-3 | Alta (performance) | Plugin aplica entidade por entidade, em série, um worker por lote; CPU de Python é o gargalo | Medido; não alterado (ver §6) |
| **P-9** | Média | Com auto-apply concorrente, um lote inteiro termina `FAILED` (os dados chegam ao NetBox pela reingestão, mas o registro mente). Duas causas: deadlocks no Postgres do Diode e deadlock de árvore (ltree) no NetBox 4.7 que o plugin devolvia como 500 | **Corrigido (M-4 + M-6)**; M-5 fecha um caminho latente |
| P-10 | A investigar | 1 lote passou dos 120s de apply (1 de 26 execuções de estresse do fork, logo após recriar o seed) | Aberto |
| P-6 | Média (dados) | Platform compartilhada entre fabricantes → cada apply reescreve `platform.manufacturer` → toda reingestão vira UPDATE + clone de drift | Documentado (comportamento de dado) |
| P-2 | Baixa | Rate limiter padrão 20 RPS / burst 1 | Não é gargalo (teto ~2000 ent/s com lote 100) |
| P-4 | Alta (segurança) | Ingester não verifica assinatura do JWT; confia no nginx | Fora do escopo; SPEC 2142 |
| P-5 | Baixa | Consumo do Redis stream serial, XAck + XDel por mensagem | Não é gargalo nesta carga (reconciler < 1% de CPU) |

### P-1 — Reenvio do `bulk-plan-apply` (crítico)

**Mecanismo.**
1. O cliente HTTP do reconciler usa o mesmo transporte para leitura (`/bulk-plan/`, `/default-branch/`) e para escrita (`/bulk-plan-apply/`): `ResponseHeaderTimeout` de 5s e `retryablehttp` com a política padrão, que **reenvia em timeout**. Esse comportamento é intencional; está documentado em `netboxdiodeplugin/client.go:195-198`.
2. O plugin aplica um lote de 100 entidades em série, uma transação por entidade. Isso leva bem mais que 5s: cerca de 9s com concorrência 1 na máquina de teste.
3. O timeout expira, mas **o worker do NetBox continua aplicando**. O reconciler reenvia o lote, e outro worker começa a aplicar as mesmas entidades em paralelo.

**Consequências, todas observadas:**
- **Duplicatas:** a primeira e a segunda cópia criam o mesmo IP com cerca de 20ms de diferença (o NetBox aceita IP duplicado por padrão). Foram de 134 a 476 IPs extras por execução com concorrência 1, e de 718 a 808 com concorrência 4.
- **Atualizações perdidas** (com concorrência 4): de 136 a 189 interfaces ficaram sem a nova descrição.
- **Log de ingestão falso:** entidades criadas pelo request que deu timeout aparecem como `NO_CHANGES`, porque o reenvio as encontrou prontas. Com concorrência 4, 100% aparecem como `FAILED`, embora os dados tenham chegado ao NetBox.
- **Trabalho dobrado:** com concorrência 4, o NetBox satura os 4 núcleos e cada lote é aplicado até 4 vezes (1 + 3 retries).
- **Instabilidade:** o throughput do upstream varia ±22% (desvio-padrão, n=6) entre execuções idênticas, conforme quantos lotes estouram o tempo. Numa das execuções, 600 entidades terminaram FAILED já na fase de criação.

**Reproduzido já com 50 entidades** (cenário smoke): 3 timeouts e 4 IPs duplicados.

### P-7 — Ordem do lote

A query de claim seleciona as linhas mais antigas (`ORDER BY id` na subquery), mas `UPDATE ... RETURNING` não preserva essa ordem. O plano de execução junta a subquery por `HashAggregate`, e as linhas voltam em ordem de hash. Numa carga nova, o IP (que leva interface e device aninhados) rodava antes do device: criava o device, e a entidade do device terminava `NO_CHANGES`. Isso aconteceu com 173 de 200 devices. O estado do NetBox fica igual, mas o registro de quem fez o quê fica errado e muda a cada execução.

### P-6 — Platform compartilhada (armadilha de dado)

O SDK copia o `manufacturer` do Device para a `platform`. Se devices de fabricantes diferentes usam a mesma platform, cada apply reescreve a platform. Cada reingestão idêntica vira UPDATE, e o reconciler clona a linha como "drift" (`CloneIngestionLogForDrift`). Não é bug de código, mas é um padrão realista de coletor: o primeiro dataset do benchmark caiu nele.

## 3. Melhorias do fork

### M-1 — `bulk-plan-apply` não é reenviado depois de chegar ao NetBox (`14f4147`)
- O cliente de apply ganhou transporte próprio, com timeout próprio: `NETBOX_DIODE_PLUGIN_APPLY_TIMEOUT_SECONDS`, padrão **120s**. Leitura e branch padrão mantêm os 5s e o retry atual.
- Retry do apply **só** quando é seguro, isto é, quando o request comprovadamente não começou: erro de conexão (dial) ou resposta 429/503 (recusa antes de processar, como o backpressure do granian).
- Arquivo: `diode-server/netboxdiodeplugin/client.go`. Testes: `client_apply_retry_test.go`, com 6 casos:
  - sem reenvio após timeout;
  - retry em 503 e 429;
  - sem retry em 500;
  - timeout de apply independente do de leitura;
  - `/bulk-plan/` continua com retry;
  - timeout inválido rejeitado.
- **Comportamento novo a saber:** se um apply realmente passar de 120s, o lote vira `FAILED` em vez de ser reenviado. O NetBox termina de aplicá-lo, e a próxima reingestão o registra como `NO_CHANGES`. É a troca deliberada: um registro conservador em vez de dados duplicados.

### M-2 — Lote aplicado na ordem de ingestão (`14ba79f`)
- `ClaimQueuedIngestionLogs` e `ClaimQueuedForAutoApply` ordenam o lote por id antes de devolvê-lo (`diode-server/dbstore/postgres/repository.go`, `sortQueuedByID`). Testes: `claim_order_internal_test.go`.
- Efeito medido: na carga inicial, as 1800 entidades ficam `APPLIED` (antes eram até 146 `NO_CHANGES` falsos com concorrência 1). Com concorrência 4 restam cerca de 23, por concorrência entre lotes diferentes, o que é esperado.

### P-9 — Lote inteiro `FAILED` sob concorrência (M-4, M-5, M-6)

Estresse: `std` sem branching, concorrência 8, lote de auto-apply 25. Antes das correções, 1 lote (25 entidades) `FAILED` em cerca de 1 a cada 8 execuções. A investigação passou por três camadas:

- **M-4 — retry de transação no Diode** (`91ecf9d`; develop `d9dd64f`). Deadlocks 40P01 entre claim, dedup e triggers de contadores do Postgres do Diode. As transações do repositório agora são reexecutadas: até 5 tentativas, com backoff e jitter. Nas execuções seguintes, os deadlocks no claim continuaram ocorrendo, mas foram absorvidos sem erro.
- **M-5 — truncate pós-commit não invalida o persist** (`cff6f70`; develop `93aa636`). Defeito latente encontrado na análise. `BulkPersistChangeSets` devolvia a falha do truncate de retenção, que roda depois do commit, como falha do persist. Com isso, um lote já `APPLIED` era marcado `FAILED`; no fluxo só de plano, os change sets eram gravados duas vezes. 3 testes unitários. Não é a causa observada.
- **M-6 — deadlock de árvore no plugin** (plugin `ae04547`, = develop). **Esta é a causa observada**, identificada em `20261115-062938`. O NetBox 4.7 (`netbox/models/ltree.py`) converte o 40P01 dos triggers de árvore em `ValidationError("The hierarchy was modified concurrently; please retry.")`. O retry do plugin só reconhecia `OperationalError`, e o lote inteiro voltava 500. Agora esse erro também recebe retry, e, esgotadas as tentativas, vira erro só da entidade (`db_deadlock_after_retries`). 9 testes; suíte v4.7 completa (1015) verde.

**Validação.** 5 execuções de estresse com M-6: nenhum 500 de árvore. Em uma delas o retry de árvore disparou e salvou o lote. Throughput `std` c=4 num A/B intercalado (3 pares, plugin antes × depois): 24,2 ± 2,3 contra 22,6 ± 1,8 ent/s, com diferença por par de −1,7 ± 3,8. **Inconclusivo:** o ruído entre execuções (~9%) é maior que qualquer efeito plausível, e no caminho sem erro a mudança não executa código novo.

**Upstream:** o defeito de M-6 está no plugin do upstream (`8c3a3bb`) com qualquer NetBox ≥ 4.7. O de M-5 está no diode do upstream.

`go test ./...` passa no fork; a suíte do plugin passou (1006 testes, NetBox 4.7.0) após a sincronização com o upstream.

## 4. Onde está o tempo (profiling)

py-spy nos 4 workers granian do NetBox durante uma carga de criação (90s, 100 Hz, 7440 amostras dentro do request do plugin):

| Fase | % do tempo do request |
|---|---|
| Apply: serializers do NetBox/DRF (`is_valid`, `create`) | 56% |
| Plan: `generate_changeset` (transformer 15%, matcher 10%) | 27% |
| Buffer de change log (snapshots) | 14% |
| Espera no Postgres (`Connection.wait`) | **~9%** |

Com concorrência 1, **só 1 dos 4 workers trabalha**. O gargalo é CPU de Python num worker só, não o banco. Por isso a concorrência 4 quase dobra o throughput no fork (11,3 → 20,1 ent/s). No upstream, a mesma mudança de configuração é desastrosa por causa do P-1.

O reconciler e o ingester (Go) usam menos de 1% de CPU em média: **não são gargalo**.

## 5. Metodologia

- **Máquina:** 12 núcleos, 31 GB, Linux 6.8, Docker 29.1.3. Limites iguais nos dois lados:
  - NetBox: 4 CPU / 4 GB, 4 workers granian;
  - Postgres de cada lado: 2 CPU / 1 GB;
  - reconciler: 2 CPU; ingester: 2 CPU.
- **Stack** (`bench/compose.yaml`): NetBox 4.7.0 + plugin (código montado), diode ingester e reconciler (binários montados), Redis e Postgres separados para NetBox e Diode. A imagem do NetBox é a mesma para os dois lados; **só o código muda**.
- **Fora do stack, nos dois lados:** Hydra e nginx. Um stub de auth (`bench/auth_stub.py`) responde token e introspect. Motivo: medir o plano de dados. O plugin cacheia a introspecção, então em produção esse custo também é amortizado.
- **Carga** (`bench/loadgen.py`): entidades montadas com as classes do SDK Python oficial e enviadas por gRPC direto ao ingester, 50 entidades por request, semente fixa.
  - Fases: `initial` (criação), `reingest` (payload idêntico) e `update` (descrição das interfaces muda).
  - Fim de fase: stream vazio e nenhuma linha em QUEUED/APPLYING por 2 leituras seguidas.
- **Início limpo:** cada execução restaura um snapshot do banco do NetBox já migrado e zera o banco do Diode.
- **Estado final** (`bench/netbox/dump_state.py`): todas as linhas dos modelos tocados, sem PKs nem timestamps, com relações renderizadas pelo objeto (inclusive GenericFK) e caminho de árvore ignorado. Duas execuções corretas produzem o mesmo hash.
- **Recursos:** amostragem contínua de `docker stats`.

## 6. Escala maior e padrão de concorrência

Cenário `large` (1000 devices, 8000 interfaces, 8000 IPs = 17 000 entidades por fase), fork com concorrência 4 (2 execuções) e concorrência 1 (1 execução):

| Fase | Concorrência 4 | Concorrência 1 | Ganho |
|---|---|---|---|
| Criação | 18,1 ent/s (~15,6 min) | 9,4 ent/s (~30 min) | 1,9× |
| Reingestão idêntica | 73,7 ± 3,2 ent/s (~3,9 min) | 43,2 ent/s (~6,6 min) | 1,7× |
| Atualização | 15,4 ± 0,4 ent/s (~18,5 min) | 5,7 ent/s (~50 min) | 2,7× |

- **0 timeouts de apply, 0 retries de deadlock no plugin, 0 erros no reconciler**, em todas as execuções.
- Contagens exatas e **estado final idêntico nas 3 execuções**: o mesmo hash com concorrência 1 e 4.
- Com concorrência 1, a atualização cai de 10,5 ent/s (`std`) para 5,7 ent/s (`large`). Com concorrência 4 a queda é bem menor (20,1 → 15,4). Não investigado. Candidato a profiling no ciclo de otimização do plugin.
- NetBox em ~253% de CPU e ≤1,5 GB de RAM (limite 4 GB). Reconciler em 20 MB.
- O throughput cai pouco em relação ao `std` (20,1 → 18,1 ent/s), ou seja, escala de forma quase linear com o volume.

**Padrão novo no fork:** `AUTO_APPLY_PROCESSOR_CONCURRENCY=4` no servidor e no `docker/sample.env` (commit `5564c69` no `develop`). O **chart Helm não foi alterado**, para continuar idêntico ao upstream (decisão do owner): quem instala via Helm deve definir

```yaml
diodeReconciler:
  config:
    autoApplyProcessorConcurrency: 4   # casar com o número de workers do NetBox
```

Regra prática: concorrência ≈ número de workers do NetBox (granian/gunicorn) dedicados à API. Acima disso, os lotes só ficam na fila do NetBox.

## 7. Limitações e próximos passos

- **O plugin não foi otimizado.** O ganho de throughput do fork vem de não fazer trabalho duplicado (M-1) e de poder usar os workers do NetBox com segurança. Otimizar o caminho do plugin (serializers, transformer, change log) é o próximo passo de performance, com o profile da §4 como mapa.
- **Uma máquina e uma versão do NetBox** (4.7.0); uma forma de dataset (devices/interfaces/IPs). O upstream não foi medido no cenário `large`: no `std` ele já não converge.
- **Binários compilados com Go 1.27.2** nos dois lados (o `go.mod` pede ≥ 1.26.10). O upstream publica imagens com 1.26.x.
- **Upstream-first:** M-1 e M-2 são candidatos naturais a PR em `netboxlabs/diode`, com este relatório como evidência. Nada foi enviado (decisão do owner).

## 8. Binários e reprodução

Estáticos, linux/amd64, `-trimpath -ldflags "-s -w"`, em `bench/out/bin/<versão>-<commit>/` (fora do git):

```
# upstream 519ccec
0a0bd4334146c91e22fe74fb557b51f109a5955669ec7e18d8b7b94fd5d9676a  ingester
b7e26eb19a5e44c5ed4d7cfa5972e88a5e6a978f5ccb8f2025532692766a12f8  reconciler
d255268541d342edf58bf0be73551bc477df9e4ed100a626badea4b097aa68f5  auth
# fork 14ba79f
b79ab28ea83137159c8a317933661a47d79d833f0bce6c63f7a2bf391295cced  ingester
3271a92361bb092ec38a8ea940cdf8b08f2dc94286fd906ee2528087f77a50b9  reconciler
62ae535130f352bd157c65fde123a1aac6daef6d1b8df4263b4153aee469d37a  auth
```

Só o `reconciler` muda de comportamento. O `auth` não depende dos pacotes alterados. O `ingester` traz `netboxdiodeplugin` só como dependência transitiva (via pacote `reconciler`), mas nunca cria o cliente do plugin, que é instanciado apenas em `cmd/reconciler/main.go`. O hash dos dois muda porque são builds de commits diferentes.

```bash
bench/run.sh --version baseline  --scenario std --runs 3          # upstream
bench/run.sh --version candidate --scenario std --runs 3          # fork
CONCURRENCY=4 bench/run.sh --version candidate --scenario std     # knobs: RATE_RPS, BATCH_SIZE, PLUGIN_TIMEOUT, NETBOX_WORKERS
bench/.venv/bin/python bench/compare.py --state bench/results/*.json
```

Resultados brutos: `bench/results/*.json` (métricas, curva de progresso, recursos) e `*.rows.json.gz` (estado final completo do NetBox).
