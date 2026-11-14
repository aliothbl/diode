# SPEC-20261113-2130: Sincronizar fork `diode` com `netboxlabs/diode` (develop, nov/2026)

**Status:** draft
**Porte:** P
**Owner:** @aliothbl
**Criada:** 2026-11-13 21:30
**Ativada:** —
**Pausada em:** —
**Concluída:** —
**Commit final:** —
**Keywords:** upstream, sync, fork, netboxlabs, fast-forward, reconciler, entityhash, tags_resolved, CVE, go-1.26.10
**Features:** upstream-sync, reconciler
**Branch:** _(a definir na ativação)_
**Programa:** `specs/programs/diode-stack-hardening.md`
**Relacionadas (irmãs, em outros repos):**
  - `diode-netbox-plugin/specs/future/SPEC-20261113-2132-sync-upstream-develop-2026-11/main.md` (**par obrigatório** de #600)
  - `diode-sdk-python/specs/future/SPEC-20261113-2131-sync-upstream-develop-2026-11/main.md`
**Origem:** diagnóstico de 2026-11-13 (`specs/DIAGNOSTICO-2026-11-13.md`, §5).
**Resumo:** Trazer para `develop` do fork os 3 commits de `upstream/develop` ainda ausentes (#598 CVE Go/x-net, #600 `tags_resolved`, #601 `source_match` no hash de dedup) por fast-forward, e alinhar `release`/tags.

## Objetivo

O fork `aliothbl/diode` não tem nenhum commit próprio em `develop` (`git rev-list upstream/develop..develop` = 0) e está 3 commits atrás de `upstream/develop`, incluindo uma correção de CVE. A sincronização é um fast-forward puro. O objetivo é zerar o atraso sem reescrever histórico, e manter o diretório `specs/` (única customização) fora do caminho de conflitos.

## Escopo

**DENTRO:**
- Fast-forward de `develop` para `upstream/develop` (`519ccec` na data do diagnóstico):
  - `7cb8d6d` #598 — Go 1.26.10 + `golang.org/x/net` v0.60.0 (CVE-2026-111943, CVE-2026-78669). Só `go.mod`/`go.sum`.
  - `175bddd` #600 — reconciler propaga `tags_resolved` do plugin (`netboxdiodeplugin/client.go`, `reconciler/changeset/changeset.go`, `reconciler/differ/differ.go`). Campo `json.RawMessage` com `omitempty` → retrocompatível com plugin sem #227.
  - `519ccec` #601 — `entityhash` passa a incluir `metadata.source_match` no hash de dedup.
- Se o fork tiver branch `release`, alinhá-la a `upstream/release` (o fork de `diode` hoje só publica `develop`).
- Trazer as tags upstream (`git fetch upstream --tags`; `git push origin --tags`) para que `helm-chart-*` e `diode-*/vX` apontem para os mesmos commits.
- Rodar `go test ./...` em `diode-server/` após o FF.

**FORA:**
- Deploy em ambiente (fica com o runbook de operação). A observação sobre #601 vai em Riscos.
- Branches upstream de trabalho (`poc/*`, `bug/*`, `fix/obs-3116-*`): não espelhar.
- Automação recorrente da sincronização → SPEC `SPEC-20261113-2140-automacao-sync-upstream`.

## Invariantes
- NUNCA reescrever histórico de `develop` (sem rebase/force-push): FF ou merge commit.
- SEMPRE manter `specs/` como único diretório customizado na raiz. Nenhum arquivo upstream é editado para acomodar o processo de specs.

## Implementação

```bash
git fetch upstream --tags
git switch develop
git merge --ff-only upstream/develop      # deve funcionar enquanto o fork não tiver commits próprios
# se houver commits de specs/ já commitados em develop: git merge --no-ff upstream/develop (sem conflito: paths disjuntos)
cd diode-server && go test ./... && cd ..
git push origin develop --follow-tags
```

Ordem de deploy com o plugin: #600 é aditivo nos dois lados. Qualquer ordem funciona, mas `tags_resolved` só chega ao plano quando **ambos** (server + plugin #227) estiverem atualizados.

### Modelo de dados
— (nenhuma migration; #601 muda a chave de hash de dedup, não o schema).

## Riscos
- #601 altera o valor de `entityhash` para entidades com `metadata.source_match`. Após o deploy, o cache de dedup não reconhece hashes antigos, e entidades já vistas podem ser reprocessadas **uma vez**. Mitigação: deploy fora de pico. Monitorar a fila do reconciler e o volume de change sets nas primeiras horas.
- Bump de Go 1.26.10 exige toolchain compatível no CI/imagens base. Mitigação: o workflow `go-test.yaml` usa `go-version-file`, então basta validar o CI verde.

## Sinais de sucesso
- `git rev-list --count develop..upstream/develop` = 0 e o scanner de container sem as duas CVEs de `x/net`.

## Critério de aceite
- [ ] `git rev-list --left-right --count develop...upstream/develop` retorna `N 0` (fork sem atraso) ← specctl check
- [ ] `go test ./...` em `diode-server/` verde após o FF ← specctl check
- [ ] Tags upstream presentes no `origin` (`git ls-remote --tags origin | grep helm-chart-diode-1.15.6`) ← specctl check
- [ ] Plugin sincronizado com #227 antes ou junto do deploy (ver SPEC irmã) | evidence: manual @aliothbl
