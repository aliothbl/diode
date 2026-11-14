# Diagnóstico — forks `diode`, `diode-sdk-python`, `diode-netbox-plugin` (2026-11-13)

**Autor:** análise assistida (Claude) para @aliothbl · **Base:** `develop` dos três forks (`054e7af`, `7844579`, `28bbb63`) · **Upstream:** `github.com/netboxlabs/*`

> **Correção de premissa:** os três repositórios são forks de projetos da **NetBox Labs** (`netboxlabs/diode*`), não da Deno Deploy. Todo o comparativo de upstream abaixo foi feito contra `netboxlabs`. O remote `upstream` foi adicionado nos três clones locais.

## 0. Sumário executivo

- **Os forks são espelhos puros.** Nenhum dos três tem commits próprios em `develop`, e não há customização a preservar hoje. Eles estão atrás do upstream: diode **−3**, sdk **−2** (e **−5** em `release`), plugin **−5** (e **−15** em `release`). Um dos commits ausentes é correção de **CVE** (Go/x-net) e outro fecha um **downgrade de TLS** no SDK.
- **A sincronização é um fast-forward**, sem conflito. Precisa ser coordenada só num ponto: diode #600 e plugin #227 (`tags_resolved`) entregam valor juntos.
- **Achados mais graves** (todos confirmados no código):
  1. O reconciler ignora **SIGTERM**, então todo deploy no Kubernetes mata o processo sem shutdown.
  2. Mensagens órfãs do Redis (PEL) nunca são reclamadas.
  3. O reset global de `APPLYING` permite **apply duplicado** em rolling update.
  4. Ingester e reconciler aceitam JWT **sem verificar assinatura** e confiam apenas no ingress.
  5. O SDK **perde entidades em silêncio** no retry quando recebe um gerador.
  6. No plugin, os **testes de CI não rodam em push para `develop`**.
  7. O plugin tem **lost update** entre plan e apply.
  8. O cache de match do plugin pode **reverter renomeações** feitas pela UI.
- **Saída deste trabalho:** 35 SPECs no padrão wynk_crm (`specs/future/*`): diode 15 (13 melhorias + sync + automação de sync), SDK 10 (9 + sync), plugin 10 (9 + sync). Também foram criados TAXONOMY, ROADMAP e `features/` por repo, além de um programa cross-repo (`specs/programs/diode-stack-hardening.md`).
- **Estratégia recomendada: upstream-first.** Como o fork não diverge, cada melhoria deve virar PR em `netboxlabs/*` e ser carregada no fork só até o merge. Divergir sem necessidade transforma cada sincronização futura em trabalho manual.

---

## 1. Visão geral — propósito e relação entre os repositórios

| Repo | Propósito | Responsabilidades |
|---|---|---|
| **diode** (Go) | Pipeline de ingestão de dados para o NetBox | `diode-auth` (OAuth2 sobre Ory Hydra) · `diode-ingester` (gRPC `Ingest` → Redis stream) · `diode-reconciler` (dedup, persistência em Postgres, plan/apply via plugin, API gRPC de leitura) · `diode-proto` (contrato protobuf, fonte do código gerado nos outros dois) · chart Helm |
| **diode-sdk-python** | Biblioteca cliente para produtores (agentes de descoberta, scripts) | Wrappers das ~106 entidades NetBox · `DiodeClient` (OAuth2 client-credentials + gRPC) · `DiodeDryRunClient` (JSON) · `DiodeOTLPClient` · chunking · CLI `diode-replay-dryrun` |
| **diode-netbox-plugin** (Django) | Braço do Diode **dentro** do NetBox | Endpoints `bulk-plan`, `bulk-plan-apply`, `default-branch` (+ legados) · transformer/matcher/differ/applier · autenticação por introspecção · telas de Settings e Client Credentials · integração com netbox-branching |

```
 Produtor ──SDK──▶ [nginx/ingress: auth_request → diode-auth/introspect]
                         │ gRPC Ingest (Bearer, escopo diode:ingest)
                         ▼
                  diode-ingester ──XADD──▶ Redis stream diode.v1.ingest-stream
                                                   │ XREADGROUP
                                                   ▼
                  diode-reconciler ──▶ Postgres (ingestion_logs, change_sets, grafo)
                         │ POST /bulk-plan[-apply]/ (OAuth2 cc, escopos netbox:read/write)
                         ▼
             diode-netbox-plugin (introspecção no diode-auth) ──▶ NetBox ORM (main ou branch)
```

**Acoplamentos que importam:**
- `diode-proto` gera o código do SDK (`make gen-diode-sdk-python`) e o do plugin.
- O contrato HTTP reconciler↔plugin (`netboxdiodeplugin/client.go` ↔ `api/urls.py`) não tem versionamento explícito. A compatibilidade depende de campos `omitempty`.
- Os wrappers do SDK (`ingester.py`) e o `plugin_utils.py` do plugin vêm de um gerador baseado no schema do NetBox 4.7.0 que **não está em nenhum dos três repos**.

## 2. Análise técnica — stack

| | diode | diode-sdk-python | diode-netbox-plugin |
|---|---|---|---|
| Linguagem | Go 1.26.4 (upstream 1.26.10) | Python ≥3.10 (CI só 3.10/3.11) | Python ≥3.10 (imagens 3.12/3.14) |
| Frameworks | grpc 1.83, protobuf 1.36, oklog/run, envconfig | grpcio, requests, opentelemetry-proto | Django/DRF do NetBox 4.4.10–4.7.x, netbox-branching (opcional) |
| Dados | Postgres 16 (pgx v5, sqlc, goose, 20 migrações) · Redis (go-redis v9) | — | Postgres do NetBox, cache Redis do NetBox |
| Auth | Ory Hydra (hydra-client-go v26), golang-jwt v5 + keyfunc | OAuth2 client-credentials | Introspecção RFC 7662 no diode-auth |
| Observab. | OTel 1.46 (exporters métricas em 1.36, *skew*), Prometheus, Sentry, pprof | Sentry (global) | logging Django |
| Codegen | buf v2 + protoc-gen-validate | `ingester_pb2*` (protobuf 5.26.1) + `ingester.py` (gerador externo) | `plugin_utils.py` 185 KB (gerador externo) |
| Empacot. | Imagens distroless; chart `charts/diode` 1.15.6 (subcharts Bitnami *legacy*, Hydra, ingress-nginx, cert-manager v1.12) | setuptools; release só sdist; semantic-release beta | setuptools; release só sdist; semantic-release |
| CI | go-test (-race), golangci-lint (só bodyclose+revive), CodeQL, Trivy, helm ct, buf breaking | pytest+cov, ruff **não bloqueante**, Trivy não bloqueante | ruff (sem pin) + matriz 4 versões NetBox (**não roda em push develop**) |
| Testes | Unit com mocks; `go test -short ./...` verde. **dbstore 0%**. Integração Hydra e e2e Python fora do CI | 192 testes (não executados nesta análise: sem venv) | ~89 mil linhas em 4 árvores quase idênticas |

## 3. Análise da implementação

### 3.1 diode
- **Pacotes:** `ingester/` · `reconciler/` (`ingestion_processor.go`, `auto_apply_processor.go`, `ingestion_log_processor.go`, `ops.go`, `backpressure.go`, `differ/`, `changeset/`) · `netboxdiodeplugin/` (cliente HTTP com rate-limit 20 rps e retryablehttp 3×) · `dbstore/postgres/` · `entityhash/` · `graph/`+`entitymatcher/`+`matching/` (grafo experimental, `ENABLE_GRAPH_DB`) · `auth/`+`authutil/` · `telemetry/`, `sentry/`, `pprof/`, `tls/`, `server/`, `migrator/`. Cerca de 28,5 mil linhas fora de `gen/` e mocks.
- **Fluxos:**
  - Ingest: validação mínima, `XADD` com Brotli opcional, admissão por memória do Redis.
  - Consumo: `XREADGROUP` → hash → `COPY` → marcação de duplicados → **ACK+XDEL sempre** (decisão #591).
  - Claim: `FOR UPDATE SKIP LOCKED`, lotes de 100.
  - Dois modos: AutoApply (`bulk-plan-apply`) ou revisão manual (`bulk-plan`, estado OPEN).
  - Backpressure por `XLEN > 100`.
  - Branch padrão com cache de 5 min e *fail-closed* (#586).
- **Configuração:** envconfig por serviço (`*/config.go`).

### 3.2 diode-sdk-python
- `client.py` (1194 linhas) concentra auth, canal, proxy, dry-run, OTLP e interceptor de path. `ingester.py` (8578 linhas) traz 110 wrappers cujo `__new__` devolve `pb.*`. Completam o pacote `chunking.py`, `exceptions.py` e `scripts/dryrun_replay.py`.
- **Fluxo do `DiodeClient`:**
  1. `logging.basicConfig`
  2. parse do target
  3. certifi
  4. **auth síncrona no construtor**
  5. canal seguro ou inseguro
  6. interceptor
  7. Sentry
- **Retry:** só para `UNAUTHENTICATED`. Não há deadline em lugar nenhum.
- **Variáveis de ambiente:** `DIODE_CLIENT_ID/SECRET`, `DIODE_SDK_LOG_LEVEL`, `DIODE_SENTRY_DSN`, `DIODE_MAX_AUTH_RETRIES`, `DIODE_CERT_FILE`, `DIODE_SKIP_TLS_VERIFY`, `DIODE_DRY_RUN_OUTPUT_DIR`, proxies.
- **Docs:** 105 exemplos, um por entidade.

### 3.3 diode-netbox-plugin
- **Pipeline:** `transformer.py` (2439 linhas: achata, deduplica por fingerprint, topo-sort, defaults, resolve refs) → `matcher.py` (2935: matchers por constraint/lógicos/especiais + cache em 2 níveis, por request e Redis com TTL 30 s) → `differ.py` (449: snapshot, merge parcial, CREATE/UPDATE/NOOP) → `applier.py` (1509: serializers DRF, `transaction.atomic()` por entidade, fallback de IntegrityError).
- **Ao redor do pipeline:** `views.py` (739: endpoints, retry em deadlock 40P01) e módulos opt-in de bypass/buffer de signals e changelog.
- **Autenticação e permissões:** introspecção com cache. O usuário de serviço não é superusuário e tem ObjectPermissions provisionadas em `post_migrate`.
- **Modelos:** `Setting` (target, branch) e `ClientCredentials` (não gerenciado).
- **Suporte declarado:** NetBox 4.4.10 a 4.7.99.

## 4. Avaliação crítica — achados

Legenda: Impacto **A**lto/**M**édio/**B**aixo · Complexidade **a**lta/**m**édia/**b**aixa. Evidência detalhada (arquivo:linha) em cada SPEC.

### 4.1 diode (D-n)

| ID | Achado | Imp. | Cx. | SPEC |
|---|---|---|---|---|
| D-1 | `run.SignalHandler(ctx, os.Interrupt, os.Kill)` não trata SIGTERM; sem graceful stop (`server/server.go:118`) | A | b | 2141 |
| D-2 | `NewUnverifiedJWTInterceptor` no ingester/reconciler: assinatura não verificada; sem NetworkPolicy | A | m | 2142 |
| D-3 | PEL do Redis nunca é reclamado (sem XAUTOCLAIM/XPENDING); consumidor nomeado por hostname | A | m | 2143 |
| D-4 | Type assertions sem checagem em mensagem do stream: poison message derruba o loop | A | b | 2143 |
| D-5 | ACK+XDEL mesmo com falha transitória do Postgres: perda silenciosa | A | m | 2143 |
| D-6 | `ResetApplyingIngestionLogs` global no startup: apply duplicado em rolling update | A | m | 2144 |
| D-7 | retryablehttp retenta POST não idempotente (`bulk-plan-apply`) com timeout de 5 s | M-A | m | 2145 |
| D-8 | Falha de transporte marca o lote inteiro como FAILED, sem retry | M | m | 2145 |
| D-9 | Regras protoc-gen-validate declaradas e nunca executadas | M | b | 2146 |
| D-10 | Caminho de erro do ingester (marshal sem return, códigos gRPC errados, payload no log) | M | b | 2146 |
| D-11 | Cardinalidade de métricas vinda do cliente sem limite | M | b | 2146 |
| D-12 | pprof exposto via `DefaultServeMux` na porta de métricas; sem `ReadHeaderTimeout` | M | b | 2150 |
| D-13 | JWT do auth sem `WithIssuer/Audience/ValidMethods` | M | b | 2142 |
| D-14 | 404 transitório em `/default-branch/` vira "sem branch" | M | b | 2145 |
| D-15 | Migrações sem lock entre réplicas; DSN por concatenação; `sslmode=disable` padrão | M | b | 2149 |
| D-16 | Sem retenção de `ingestion_logs`/`change_sets`; paginação OFFSET | M | m | 2151 |
| D-17 | Hash de dedup sem versão (upgrade de proto replaneja tudo) | B-M | m | 2151 |
| D-18 | `dbstore/postgres` com 0% de teste; integração e e2e fora do CI | A | m | 2147 |
| D-19 | Chart sem probes/PDB/NetworkPolicy, `busybox:latest`, `bitnamilegacy` + `allowInsecureImages`, cert-manager EOL | M-A | m | 2148 |
| D-20 | Actions sem pin por SHA; `server-release.yaml` chama workflows reutilizáveis `netboxlabs/diode/...@develop` (o fork executa código da branch móvel do upstream); ferramentas `@latest`; lint fraco | M | b | 2152 |
| D-21 | Consumidor sequencial; upsert de grafo por entidade | B-M | m | 2151 |
| D-22 | TODOs: `SDKVersion="0.1.0"` fixo; escopo único por RPC (hoje correto, pois os 3 RPCs são de leitura com `diode:read`; a SPEC propõe mapa método→escopo *default-deny* para RPCs futuros) | B | b | 2150 |
| D-23 | Sem doc de arquitetura, runbook e modelo de ameaça | B | b | 2153 |

> Nota sobre D-5: o "ack regardless" é uma **decisão intencional do upstream** (#591), que evita que o backpressure trave. A SPEC 2143 preserva essa garantia: em vez de não dar ACK, ela leva as mensagens para uma DLQ e reclama as órfãs com XAUTOCLAIM.

### 4.2 diode-sdk-python (S-n)

| ID | Achado | Imp. | Cx. | SPEC |
|---|---|---|---|---|
| S-1 | `DIODE_SKIP_TLS_VERIFY` + `grpcs://` cai em canal **texto claro** (token exposto) | A | m | **upstream #114** → 2131 |
| S-2 | `IngestRequest(entities=…)` montado dentro do loop de retry: com gerador, o retry envia lote vazio e recebe OK | A | b | 2200 |
| S-3 | `DIODE_MAX_AUTH_RETRIES` ignorado (`_get_optional_config_value` nunca lê o env); `0` → RuntimeError | M | b | 2202 |
| S-4 | Sem deadline em `Ingest` e no POST de token (construtor pode travar) | A | b | 2201 |
| S-5 | Sem retry para UNAVAILABLE/DEADLINE; `id` do request muda a cada tentativa | M | m | 2201 |
| S-6 | Auth sem lock; sem renovação proativa | M | m | 2203 |
| S-7 | `logging.basicConfig` e `sentry_sdk.init` global (sample 1.0) dentro de biblioteca | M | b | 2204 |
| S-8 | README diz "system certificates", mas o código usa sempre o certifi | M | b | 2204 |
| S-9 | Proxy com semânticas diferentes entre gRPC e auth; parse IPv6 quebrado | B | m | 2206 |
| S-10 | `DiodeClientError` sem mensagem e fora de `BaseError` | M | b | 2205 |
| S-11 | Wrappers com `__new__` enganam type checkers; `py.typed` sem mypy | M | m | 2205 |
| S-12 | Dry-run: precedência env>arg, nome de arquivo não único, replay perde metadata | B | b | 2202 |
| S-13 | `protobuf` não declarado; `setuptools>=43` inválido para PEP 621 | M | b | 2205 |
| S-14 | CI: **mesmo filtro `push.branches` só-negativo do plugin (testes não rodam em push)**, lint não bloqueante, publish antes da tag, só sdist, `set-output`, sem pins | M-A | b | 2207 |
| S-15 | Estimativa de chunk ignora o overhead por entidade; entidade maior que o limite passa sem aviso (sem perda de dados: o iterável já é materializado) | B | b | 2206 |
| S-16 | Gerador do `ingester.py` fora dos repos (sem drift hoje, mas sem verificação) | M | m | 2208 |

### 4.3 diode-netbox-plugin (P-n)

| ID | Achado | Imp. | Cx. | SPEC |
|---|---|---|---|---|
| P-1 | **Lost update**: o applier não compara `before` com o estado atual; o plan grava o pós-change completo | A | m | 2210 |
| P-2 | Acerto no cache Redis de match só checa `pk` existente: rename via UI é revertido | A | m | 2211 |
| P-3 | Sem header, a resposta é anotada com a branch do Setting, mas leitura e escrita vão para main | A | b | 2212 |
| P-4 | TTL de introspecção `exp-iat` (token aceito após expirar); sem `is_active`; `aud` string | M | b | 2213 |
| P-5 | Tipos graváveis incluem `extras.customfield`, `customlink`, `core.managedfile` | M | m | 2214 |
| P-6 | Bypass de change logging pode silenciar webhooks/event rules (não documentado) | M | b | 2215 |
| P-7 | Caches de CF por processo, sem invalidação cruzada | M | m | **upstream #219/#222** → 2132 |
| P-8 | `Branch.objects.get` por entidade (N+1) | B | b | 2212 |
| P-9 | `split(".")` sem try (500), `print_exc`, `requests` sem timeout, lote sem limite | B | b | 2215 |
| P-10 | 4 árvores de teste (~89 mil linhas), com drift entre versões | A (manut.) | m | 2217 |
| P-11 | `plugin_utils.py` gerado sem gerador no repo | M | m | 2218 |
| P-12 | `push.branches: ["!release"]` só com padrão negativo, então **o CI não roda em push**; permissões amplas; publish antes da tag | M-A | b | 2216 |
| P-13 | `grpcio/protobuf` declarados e não usados; `requests` usado e não declarado; Docker não instala via pyproject | M | b | 2216 |
| P-14 | Sem type checker; 7 funções `noqa: C901` | B | — | (não especificado; ver §6) |

## 5. Sincronização com o projeto original (netboxlabs)

### 5.1 Estado

| Repo | `develop` fork vs upstream | `release` | Commits próprios | Merge-base |
|---|---|---|---|---|
| diode | **0 / 3** | fork não tem `release` (upstream: 34 à frente de develop do fork) | **nenhum** | `054e7af` (2026-09-25) |
| diode-sdk-python | **0 / 2** | **0 / 5** | **nenhum** | `7844579` (2026-09-23) |
| diode-netbox-plugin | **0 / 5** | **0 / 15** | **nenhum** | `28bbb63` (2026-10-02) |

### 5.2 Commits do upstream ainda não incorporados (`develop`)

| Repo | Commit | Data | Tipo | Conteúdo | Observação |
|---|---|---|---|---|---|
| diode | `7cb8d6d` #598 | 10-21 | **segurança** | Go 1.26.10 + x/net v0.60.0 (CVE-2026-111943, CVE-2026-78669) | só go.mod/go.sum |
| diode | `175bddd` #600 | 10-27 | feature | reconciler propaga `tags_resolved` do plugin | `omitempty`, retrocompatível; **par do plugin #227** |
| diode | `519ccec` #601 | 11-05 | correção | `metadata.source_match` entra no hash de dedup | muda os hashes: reprocessamento único pós-deploy |
| sdk | `2db9b1f` #114 | 10-06 | **segurança** | TLS mantido com `DIODE_SKIP_TLS_VERIFY` (tunnel local `_skip_verify_tunnel.py`) | quebra quem dependia do texto claro; Windows exige `..._ALLOW_LOOPBACK`; atenção com pre-fork |
| sdk | `44ff22f` #117 | 10-21 | dependência | `certifi>=2026.10.19` | pode conflitar com ambientes travados |
| plugin | `9947f12` #219 | 10-14 | correção | invalidação de cache de CF entre workers (`customfield_version.py`) | requer cache compartilhado (Redis) |
| plugin | `c209483` #222 | 10-15 | correção | clearer que falha não interrompe os demais | |
| plugin | `208a5db` #223 | 10-21 | dependência | `certifi>=2026.10.19` | |
| plugin | `b675a5c` #225 | 10-23 | limpeza | remove Brotli não usado | |
| plugin | `8c3a3bb` #227 | 10-27 | feature | `tags_resolved` nas mudanças planejadas | **par do diode #600** |

### 5.3 Branches do fork
- `diode-sdk-python: origin/fix/enghlp-1703-skip-tls-verify-parity` tem 14 commits pré-squash do trabalho que entrou como #114. Upstream já apagou a branch. **Remover** (o diff dela apaga `tests/test_skip_verify_tunnel.py`).
- `diode-netbox-plugin: origin/mon-359-cooling-components-template-instantiated` é idêntica à upstream (0/0). Pode ser mantida ou apagada.

### 5.4 Recomendação de processo
1. Fast-forward imediato dos três (SPECs **2130 / 2131 / 2132**), com plugin e server na mesma janela de deploy.
2. Automação diária via `merge-upstream` com PR em caso de conflito, e os workflows de release do upstream desabilitados no fork (SPEC **2140**). O token precisa de `workflows:write`.
3. Customizações só em **diretórios novos** (`specs/`, `.github/workflows/fork-*.yaml`). Melhorias de código vão **upstream-first**.

## 6. Melhorias consolidadas — priorização

Critério: ondas por impacto (risco de perda de dados, segurança, disponibilidade) × complexidade. Dentro da onda, as dependências do programa cross-repo vêm primeiro.

| Onda | SPEC | Repo | Título | Imp. | Cx. | Porte |
|---|---|---|---|---|---|---|
| **0 — já** | 2130 / 2131 / 2132 | todos | Sync com upstream (CVE, TLS, CF cache, tags_resolved) | A | b | P |
| 0 | 2140 | todos | Automação de sync + desligar release no fork | A | b | M |
| **1 — quick wins críticos** | 2141 | diode | Tratar SIGTERM / graceful shutdown | A | b | P |
| 1 | 2200 | sdk | Retry preserva entidades (perda silenciosa) | A | b | P |
| 1 | 2216 | plugin | CI roda em push + higiene de deps | A | b | P |
| 1 | 2212 | plugin | Branch coerente sem header + N+1 | A | b | P |
| 1 | 2213 | plugin | Endurecer introspecção (TTL, is_active, aud) | M | b | P |
| 1 | 2201 | sdk | Deadlines + retry de transporte | A | m | M |
| 1 | 2143 | diode | Stream: PEL reclaim, DLQ, poison message | A | m | M |
| **2 — consistência e segurança** | 2142 | diode | JWT verificado no ingester/reconciler + NetworkPolicy | A | m | M |
| 2 | 2144 | diode | Lease em claims APPLYING (programa) | A | m | M |
| 2 | 2210 | plugin | Concorrência otimista no apply (programa) | A | m | M |
| 2 | 2145 | diode | Retry seguro do apply (programa; depende de 2144 + 2210) | M-A | m | M |
| 2 | 2211 | plugin | Revalidar acerto do cache de match | A | m | M |
| 2 | 2147 | diode | Testes de dbstore + integração no CI | A | m | M |
| **3 — endurecimento** | 2146 | diode | Validação de entrada no ingester | M | b | P |
| 3 | 2149 | diode | Lock de migração + DSN segura | M | b | P |
| 3 | 2150 | diode | Superfície HTTP (pprof, timeouts) | M | b | P |
| 3 | 2152 | diode | CI supply chain (pins, gosec) | M | b | P |
| 3 | 2148 | diode | Helm hardening | M-A | m | M |
| 3 | 2202 | sdk | Precedência de config + dry-run | M | b | P |
| 3 | 2204 | sdk | Sem efeitos globais (logging/Sentry) + CA doc | M | b | P |
| 3 | 2207 | sdk | CI/release hardening | M | b | P |
| 3 | 2203 | sdk | Auth thread-safe + renovação proativa | M | m | M |
| 3 | 2215 | plugin | Robustez de endpoints + doc do bypass | M | b | P |
| 3 | 2214 | plugin | Allowlist de tipos graváveis | M | m | M |
| **4 — estrutural** | 2151 | diode | Retenção, paginação keyset, hash versionado, throughput | M | m | M |
| 4 | 2153 | diode | Docs de arquitetura, runbook, ameaças | B | b | P |
| 4 | 2205 | sdk | Tipagem, exceções, dependências | M | m | M |
| 4 | 2206 | sdk | Proxy unificado + chunking | B | m | P |
| 4 | 2208 | sdk | Verificação de drift do codegen | M | m | M |
| 4 | 2218 | plugin | Gerador do plugin_utils versionado | M | m | M |
| 4 | 2217 | plugin | Unificar árvores de teste por versão | A (manut.) | m | G |

**Não especificado (decisão consciente):** a adoção de type checker no plugin (P-14) só vale a pena depois de 2217. Fica como candidata a SPEC futura.

## 7. Onde estão as SPECs

- `diode/specs/future/SPEC-20261113-21{30,40..53}-*/main.md` · programa `diode/specs/programs/diode-stack-hardening.md` · `diode/specs/ROADMAP.md`
- `diode-sdk-python/specs/future/SPEC-20261113-2131,2200..2208-*/main.md` · `ROADMAP.md`
- `diode-netbox-plugin/specs/future/SPEC-20261113-2132,2210..2218-*/main.md` · `ROADMAP.md`
- Memória viva por área: `specs/features/*.md` em cada repo · vocabulário: `specs/TAXONOMY.md`

## 8. Método e limitações

- Leitura estática dos três repos em `develop`, mais `git diff develop upstream/develop`. `go test -short ./...` no `diode-server` passou.
- O `pytest` do SDK e do plugin **não foi executado**: não há venv/imagem pronta no host. A avaliação de testes nesses dois repos é por leitura.
- Os achados de maior gravidade (D-1, D-2, D-3, D-5, P-2, P-4, P-12, S-2, S-3) foram reconferidos linha a linha. P-6 (bypass silenciando webhooks) está marcado como hipótese até a SPEC 2215 confirmar no código do NetBox.
- Os números de linha valem para `develop` na data. As SPECs do SDK citam `upstream/develop`, porque o #114 reescreve `client.py`.
