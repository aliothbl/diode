# SPEC-20261113-2149: Lock de migrações e conexão segura com o Postgres

**Status:** draft
**Porte:** P
**Owner:** @aliothbl
**Criada:** 2026-11-13 21:49
**Ativada:** —
**Pausada em:** —
**Concluída:** —
**Commit final:** —
**Keywords:** goose, session-locker, migrations, dsn, pgx-parseconfig, sslmode, postgres, startup-race
**Features:** persistence
**Branch:** _(a definir na ativação)_
**Depende de:** SPEC-20261113-2130-sync-upstream-develop-2026-11
**Programa:** —
**Impacto:** médio · **Complexidade:** baixa
**Estratégia upstream:** upstream-first — abrir PR em netboxlabs/diode; carregar no fork só até o merge; ao sincronizar, descartar o commit local se o upstream absorveu
**Origem:** diagnóstico 2026-11-13 (specs/DIAGNOSTICO-2026-11-13.md, achado D-15)
**Resumo:** Cada réplica do reconciler roda `goose Up` no startup sem lock de sessão. A DSN é montada por `fmt.Sprintf` em formato key=value, sem escapar (uma senha com espaço ou aspas quebra), e o default de `POSTGRES_SSL_MODE` é `disable`. Adicionar `WithSessionLocker`, montar a DSN de forma estruturada e mudar o default para `prefer`.

## Objetivo

Startups concorrentes aplicam cada migration exatamente uma vez. Credenciais com qualquer caractere funcionam. O default de transporte deixa de ser texto puro.

## Escopo

**DENTRO:**
- `diode-server/migrator/migrator.go:43`: `goose.NewProvider("", db, migrationsFS, goose.WithStore(store), goose.WithSessionLocker(locker))`, com `locker, _ := lock.NewPostgresSessionLocker()` (`github.com/pressly/goose/v3/lock`, disponível em goose v3.24.3, `go.mod:21`).
- `diode-server/cmd/reconciler/main.go:79`: substituir o `fmt.Sprintf("host=%s port=%d user=%s password=%s ...")` por construção via `url.URL{Scheme:"postgres", User: url.UserPassword(u,p), Host: net.JoinHostPort(h, port), Path: db, RawQuery: "sslmode="+mode}`, ou via `pgx.ParseConfig` + campos atribuídos.
- `diode-server/reconciler/config.go:27`: `POSTGRES_SSL_MODE` default `disable` → `prefer`. Validar contra o conjunto aceito (`disable|allow|prefer|require|verify-ca|verify-full`).
- Caminho de migrations fixo `"/etc/diode/migrations"` (`cmd/reconciler/main.go:283`): tornar configurável (`MIGRATIONS_PATH`, mesmo default) ou embutir com `embed.FS`. O default proposto é `embed`, que elimina a dependência do layout da imagem.
- Teste: 3 goroutines chamando `Run(OperationUp)` no mesmo banco (testcontainers, reusando o helper da SPEC-20261113-2147) e senha com espaço, aspas e `@`.

**FORA:**
- Separar migrations em Job/initContainer do Helm (pode vir depois; com o locker deixa de ser necessário).

## Invariantes
- NUNCA aplicar a mesma migration em paralelo.
- NUNCA interpolar credenciais em string de conexão sem escape.

## Implementação
Mudanças locais de poucas linhas. O `prefer` mantém compatibilidade com Postgres sem TLS (o Bitnami do chart) e passa a usar TLS quando disponível.

### Modelo de dados
— (tabela de versões do goose inalterada; o locker usa advisory lock de sessão).

<!-- Alternativas: (a) Job de migração separado no Helm — rejeitada por ora: muda o fluxo de deploy e o docker-compose; o locker resolve com menos superfície; (b) default require — rejeitada: quebraria a instalação padrão do chart com o Postgres embutido. -->

## Riscos
- Com `embed`, o volume `/etc/diode/migrations` do Dockerfile fica órfão. Mitigação: remover a cópia no Dockerfile no mesmo PR.

## Sinais de sucesso
- `kubectl scale --replicas=3` no reconciler em banco novo sobe sem erro de "relation already exists".

## Critério de aceite
- [ ] `WithSessionLocker` configurado e teste de concorrência verde ← specctl check
- [ ] DSN montada sem `fmt.Sprintf` com senha; teste com senha especial ← specctl check
- [ ] Default de `POSTGRES_SSL_MODE` = `prefer`, com validação ← specctl check
