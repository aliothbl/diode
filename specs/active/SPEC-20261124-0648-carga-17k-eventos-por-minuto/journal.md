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
