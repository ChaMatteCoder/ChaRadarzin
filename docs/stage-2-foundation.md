# Etapa 2 — Fundação multiusuário

## Limite arquitetural

O MVP continua em `app/`, com o catálogo Excel e o SQLite originais. A
aplicação web vive em `chadaradzin/`, `tenancy/` e `monitoring/` e usa banco
próprio. Nenhuma tabela do MVP é alterada pelas migrações Django.

## Identidade e tenant

- Cada conta comum referencia exatamente um tenant; contas administrativas
  podem existir sem tenant para operação do Django Admin.
- A identidade autenticada é a tupla OIDC `(issuer, subject)`.
- O `telegram_user_id` do escopo `profile` é armazenado separadamente e também
  é único. Uma tentativa de religar qualquer lado da associação é rejeitada.
- Usuários criados pelo Telegram recebem senha inutilizável. Sessões usam o
  backend do Django e cookies `HttpOnly`.
- O cliente OIDC usa discovery oficial, state e nonce da Authlib, PKCE S256 e
  claims do ID token; não depende de endpoint UserInfo.

## Isolamento

Modelos de domínio carregam `tenant_id`. Consultas do painel e da API partem de
`for_user()` e nunca de um queryset global. A API acrescenta uma permissão de
tenant no nível da requisição e outra no nível do objeto. Um identificador de
outro tenant resulta em `404`, sem confirmar a existência do recurso.

A API da fundação é somente leitura:

- `GET /api/v1/me/`
- `GET /api/v1/produtos/`
- `GET /api/v1/produtos/{id}/`

Escrita de produtos, links e preferências permanece reservada para a Etapa 4.

## Bancos e migração

- Desenvolvimento: SQLite novo em `data/chadaradzin_web.sqlite3`.
- Produção: PostgreSQL obrigatório por `DATABASE_URL`, com Psycopg 3.
- Origem legada: aberta com SQLite `mode=ro&immutable=1`.
- Destino: produtos derivados, fontes, execuções, observações, erros e
  tentativas de notificação ficam associados a um tenant explícito.
- Idempotência: `(tenant, sha256 da origem)` identifica o lote.
- Reconciliação: contagens de origem e destino são persistidas no lote.
- Privacidade: somente o nome do arquivo é persistido, não seu caminho local.
- Alertas: o lote e o tenant recebem cursores; dados importados não devem gerar
  notificações retroativas quando o motor novo for ativado.

## Fora do escopo desta etapa

- managed bots, tokens e webhooks;
- criação e edição de produtos no painel;
- filas, workers e scheduler distribuído;
- execução do motor novo de alertas;
- notificações por bots pessoais.

Essas capacidades continuam nos estágios definidos no plano original.
