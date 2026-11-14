# Programa: diode-stack-hardening

**Owner:** @aliothbl

> Programa **cross-repo** (diode, diode-sdk-python, diode-netbox-plugin). Ele reúne só as SPECs cuja ordem depende de outro repositório. As demais SPECs ficam no `specs/ROADMAP.md` de cada repo.
> Notação de nó em outro repo: `<repo>:SPEC-<id>`. Bloqueia/ordem são DERIVADOS (wynk_crm rules/programs.md), nunca escritos aqui.

- SPEC-20261113-2130-sync-upstream-develop-2026-11 | depende de: —
- diode-sdk-python:SPEC-20261113-2131-sync-upstream-develop-2026-11 | depende de: —
- diode-netbox-plugin:SPEC-20261113-2132-sync-upstream-develop-2026-11 | depende de: —
- SPEC-20261113-2140-automacao-sync-upstream | depende de: SPEC-20261113-2130, diode-sdk-python:SPEC-20261113-2131, diode-netbox-plugin:SPEC-20261113-2132
- SPEC-20261113-2144-lease-claims-applying | depende de: SPEC-20261113-2130
- diode-netbox-plugin:SPEC-20261113-2210-concorrencia-otimista-apply | depende de: diode-netbox-plugin:SPEC-20261113-2132
- SPEC-20261113-2145-retry-seguro-apply-plugin | depende de: SPEC-20261113-2144, diode-netbox-plugin:SPEC-20261113-2210

## Notas de coordenação
- 2130 + 2132 entram na **mesma janela de deploy**: diode #600 e plugin #227 só entregam `tags_resolved` juntos. São retrocompatíveis isoladamente.
- 2210 (plugin) define o contrato de conflito (HTTP 409 + corpo) que 2145 (server) consome. Mudança nesse contrato exige atualizar as duas SPECs.
