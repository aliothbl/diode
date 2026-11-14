# Programa: diode-stack-hardening

**Owner:** @aliothbl

> Programa **cross-repo** (diode, diode-sdk-python, diode-netbox-plugin, netbox-branching). Ele reúne só as SPECs cuja ordem depende de outro repositório. As demais SPECs ficam no `specs/ROADMAP.md` de cada repo.
> Notação de nó em outro repo: `<repo>:SPEC-<id>`. Bloqueia/ordem são DERIVADOS (wynk_crm rules/programs.md), nunca escritos aqui.

- SPEC-20261113-2130-sync-upstream-develop-2026-11 | depende de: —
- diode-sdk-python:SPEC-20261113-2131-sync-upstream-develop-2026-11 | depende de: —
- diode-netbox-plugin:SPEC-20261113-2132-sync-upstream-develop-2026-11 | depende de: —
- SPEC-20261113-2140-automacao-sync-upstream | depende de: SPEC-20261113-2130, diode-sdk-python:SPEC-20261113-2131, diode-netbox-plugin:SPEC-20261113-2132
- SPEC-20261113-2144-lease-claims-applying | depende de: SPEC-20261113-2130
- diode-netbox-plugin:SPEC-20261113-2210-concorrencia-otimista-apply | depende de: diode-netbox-plugin:SPEC-20261113-2132
- SPEC-20261113-2145-retry-seguro-apply-plugin | depende de: SPEC-20261113-2144, diode-netbox-plugin:SPEC-20261113-2210
- netbox-branching:SPEC-20261114-1741-http-branch-indisponivel | depende de: —
- SPEC-20261114-1748-retry-branch-indisponivel | depende de: netbox-branching:SPEC-20261114-1741
- diode-netbox-plugin:SPEC-20261114-1747-bump-netbox-branching-1-2-3 | depende de: —

## Notas de coordenação
- 2130 + 2132 entram na **mesma janela de deploy**: diode #600 e plugin #227 só entregam `tags_resolved` juntos. São retrocompatíveis isoladamente.
- 2210 (plugin) define o contrato de conflito (HTTP 409 + corpo) que 2145 (server) consome. Mudança nesse contrato exige atualizar as duas SPECs.
- 1741 (netbox-branching) define o contrato HTTP de branch indisponível (503 + `Retry-After` / 409 / 404) que 1748 (diode) consome. Mudar o contrato exige atualizar as duas SPECs. 1748 é retrocompatível com o netbox-branching atual: um 400 continua sendo tratado como hoje.
