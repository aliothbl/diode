# Journal — SPEC-20261124-0648-carga-17k-eventos-por-minuto

## SNAPSHOT
- **Fase:** 0 concluída (mistura definida); fase 1 a iniciar.
- **Branch:** `perf/17k-por-minuto` (diode), a partir de `perf/bench-vs-upstream` @ `7708929`. Só local.
- **Próximo passo:** fase 1. `bench/loadgen.py` com `--rate/--mix/--duration`, cenário `sustained` no `run.sh` com amostragem do backlog por segundo e drenagem com verificação de estado.

## Decisões
- **DEC-1 — mistura de carga 90/7/3 (2026-11-24 06:54).** 90% repetido, 7% update, 3% create, a 17.000 eventos/min (≈283 ent/s). Owner: "pode usar 90/7/3, commita e ativa". Em números: ≈255 ent/s repetidos, ≈20 ent/s update e ≈8,5 ent/s create.
- **DEC-2 — critério de "sustentado" (2026-11-24 06:54).** Vale a proposta do `main.md`, aceita com a ativação: janela de 10 min a 17.000/min; no fim da janela o backlog (QUEUED + APPLYING em `ingestion_logs` + stream do Redis) é ≤ 1 minuto de carga (≤ 17.000) e não cresce nos últimos 5 minutos; depois, drenagem completa e estado final correto.

## LOG
- 2026-11-24 06:54 — SPEC criada às 06:48 em `specs/future/` e ativada no branch novo `perf/17k-por-minuto`. Fase 0 fechada com DEC-1 e DEC-2. Leitura inicial da conta: com 90% repetido, sem a fase 3 todo evento ainda passa pelo plan do plugin (`BulkMarkDuplicates` reenfileira `prev_state IN (3, 4, 5)`), e o reingest idêntico medido é 73,7 ent/s. A expectativa é que a fase 2 sozinha não chegue a 283 ent/s e que a fase 3 seja a alavanca principal nesta mistura. Isso é hipótese, a ser medida.
- 2026-11-24 07:52 — **Fase 1, implementação.**
  - `bench/loadgen.py`: fase `sustained` (`--rate`, `--mix repeat:update:create`, `--duration`, `--tick`). A carga é enviada em ticks de 1 s: create e update vão em requests próprios, um por tipo por tick, para que suas ingestion logs sejam achadas por `request_id`; repetidos vão em requests de `--batch`.
  - Inventário base (`Inventory`): é o `large` por padrão. O IP aponta para a interface **sem descrição**, porque o update muda descrições e um IP repetido não pode carregar uma cópia velha da interface. Por isso o inventário do `sustained` não é comparável byte a byte com o `large`.
  - Update: nova descrição (`uplink i (revN)`, N global) numa interface base sorteada. Create: devices novos inteiros, entidade por entidade. Repetido: a versão atual de uma entidade base sorteada.
  - `bench/verify_sustained.py`: confere no dump do NetBox as contagens esperadas, IPs duplicados e a última descrição de cada interface atualizada. `run.sh` ganhou os cenários `sustained` e `sustained-smoke` (este só valida o harness, não mede).
  - Gerar 1 min de eventos a 17k/min custa ≈0,45 s de CPU no gerador.
- 2026-11-24 07:52 — **Achado que afeta o DEC-2 (proposta, depende do owner).** Um repetido cuja linha ainda está QUEUED só incrementa `duplicate_count` (`BulkMarkDuplicates` mantém o estado se `prev_state` não for 3/4/5). Então o backlog (QUEUED+APPLYING) nunca passa do número de entidades distintas (≈17k no inventário `large`), e "backlog ≤ 17.000" fica praticamente garantido mesmo com o pipeline atrás da carga. Proposta: o critério de "sustentado" passa a ser a **latência de create e update** (envio → todas as linhas do request em estado terminal), com p95 estável (2ª metade da janela ≤ 1ª metade × 1,5) e p95 ≤ 60 s, além do backlog e do estado final. O loadgen já mede as duas coisas (`latency.*`, `backlog_*`). Fica registrado como proposta até o OK do owner.
- 2026-11-24 08:00 — **`sustained-smoke` ok** (`bench/results/20261124-075416-candidate-sustained-smoke-run1.json`, c=4): 600 eventos/min por 60 s, taxa enviada 600,0/min, 0 ticks atrasados; latência de create e update p95 ≈2,0 s (piso ≈ granularidade do tick + poll do reconciler); drenagem em 3 s; 0 FAILED; check do estado final ok (24 devices, 47 interfaces, 47 IPs, 0 IPs duplicados, 24 descrições atualizadas corretas). Próximo: `sustained` real a 17.000/min com o código atual (c=4).
