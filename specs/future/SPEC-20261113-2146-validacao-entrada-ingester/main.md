# SPEC-20261113-2146: Validação completa da entrada no ingester e cardinalidade de métricas

**Status:** draft
**Porte:** P
**Owner:** @aliothbl
**Criada:** 2026-11-13 21:46
**Ativada:** —
**Pausada em:** —
**Concluída:** —
**Commit final:** —
**Keywords:** protoc-gen-validate, validateall, invalidargument, max_items, uuid, marshal, nil-entity, sentry-noise, metric-cardinality, otel-attributes
**Features:** ingester, observability
**Branch:** _(a definir na ativação)_
**Depende de:** SPEC-20261113-2130-sync-upstream-develop-2026-11
**Programa:** —
**Impacto:** médio · **Complexidade:** baixa
**Estratégia upstream:** upstream-first — abrir PR em netboxlabs/diode; carregar no fork só até o merge; ao sincronizar, descartar o commit local se o upstream absorveu
**Origem:** diagnóstico 2026-11-13 (specs/DIAGNOSTICO-2026-11-13.md, achados D-9, D-10 e D-11)
**Resumo:** As regras PGV do `ingester.proto` (`max_items: 1000`, `uuid`, `max_len: 255`, `timestamp.lt_now`) são geradas mas nunca executadas. O handler `Ingest` tem bugs no caminho de erro (marshal sem return, entidade nil enfileirada, erros como `codes.Unknown` indo para o Sentry, payload inteiro no log). E campos livres do cliente viram atributos de métrica sem limite.

## Objetivo

Rejeitar cedo e com o código gRPC correto toda requisição fora do contrato do proto, sem ruído no Sentry e sem explosão de séries de métrica.

## Escopo

**DENTRO:**
- `diode-server/ingester/component.go:144`: após `validateRequest(in)` (`:240-268`, checks manuais), chamar `in.ValidateAll()` (gerado em `gen/diode/v1/diodepb/ingester.pb.validate.go`). Erro → `status.Error(codes.InvalidArgument, err.Error())`. As regras estão em `diode-proto/diode/v1/ingester.proto:27,144-161`.
- `validateRequest` passa a retornar `status.Error(codes.InvalidArgument, ...)`, e não `fmt.Errorf` (que vira `codes.Unknown`).
- O `sentry.CaptureError` de `:144-161` só é chamado para erros que **não** sejam `InvalidArgument`/`ResourceExhausted`.
- `:170-174`: falha em `proto.Marshal(in)` hoje só loga (com o request inteiro) e segue enfileirando `encodedRequest` vazio. Adicionar `return nil, status.Error(codes.Internal, "")` e logar apenas `request_id` e a contagem de entidades.
- `:176-181`: entidade nil é anotada em `errs`, mas o request inteiro (com a nil) é enfileirado. Rejeitar com `InvalidArgument` (comportamento estrito) **ou** filtrar as nil antes do marshal. Decisão na ativação: o default proposto é filtrar e devolver em `errs`, preservando a compatibilidade com SDKs.
- Logs de erro (`:173`, `:200`, `:222`): remover o campo `"request", in` e `"value", msg`, que levam o payload completo (dados de inventário) ao log.
- Cardinalidade: `sdk_name`, `sdk_version`, `producer_app_name` e `producer_app_version` (`:134-141`) ganham normalização: truncar em 64, colapsar caracteres fora de `[A-Za-z0-9._-]` e mapear para `"other"` acima de `INGESTER_METRIC_PRODUCER_MAX` valores distintos (LRU de 200).

**FORA:**
- Validação semântica de entidades (referências NetBox), que é responsabilidade do plugin.
- Mudança no `.proto`.

## Invariantes
- NUNCA enfileirar no stream uma requisição que viole as regras PGV.
- NUNCA logar o payload de entidades em nível ≥ INFO.

## Implementação
Mudança local em `ingester/component.go` e um helper `normalizeLabel` em `telemetry/`. Testes tabulares em `ingester/component_test.go`: 1001 entidades, id não-UUID, `producer_app_name` com 300 chars, timestamp no futuro e marshal com falha (mock). Cada caso tem seu código gRPC esperado.

### Modelo de dados
—

<!-- Alternativas: (a) interceptor genérico de validação para todos os serviços — rejeitada agora: só o Ingest recebe input externo grande; pode vir depois; (b) rejeitar nil estritamente — deixada como opção na ativação por risco de quebrar SDKs antigos. -->

## Riscos
- SDKs que enviam `id` não-UUID passam a ser rejeitados. Mitigação: confirmar que o `diode-sdk-python` usa `uuid4()` (já usa, `client.py:502`) e o `diode-sdk-go`. Registrar no CHANGELOG.
- O limite de 1000 entidades quebra produtores grandes que não fazem chunk. Mitigação: o SDK Python já tem `create_message_chunks`. Documentar.

## Sinais de sucesso
- Os eventos de Sentry do ingester caem para ~0 em regime, e o número de séries de `diode_ingester_*` fica estável quando surgem produtores novos.

## Critério de aceite
- [ ] `ValidateAll()` chamado e testes tabulares verdes ← specctl check
- [ ] Erros de validação retornam `codes.InvalidArgument` e não geram evento Sentry ← specctl check
- [ ] Marshal com falha retorna erro e nada é enfileirado ← specctl check
- [ ] Nenhum log com o request completo no ingester (`grep -n '"request", in' ingester/component.go` vazio) ← specctl check
