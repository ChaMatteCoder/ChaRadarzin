# Portões de validação da integração Telegram

Estes pontos devem ser comprovados em bots de teste antes de conectar a nova
arquitetura aos bots usados pelo MVP.

## Identidade e autorização

- Tratar `(iss, sub)` do OpenID Connect como identidade de autenticação.
- Armazenar separadamente o `id` do escopo `profile` como
  `telegram_user_id`; `sub` e `id` não são equivalentes.
- Validar `state`, nonce, PKCE S256, assinatura via JWKS, `iss`, `aud` e
  expiração do ID token. A biblioteca escolhida deve funcionar sem endpoint
  UserInfo separado.
- Usar tipo inteiro de 64 bits ou string para identificadores Telegram.

## Criação e vínculo do bot

- Correlacionar a atualização `managed_bot` pelo `telegram_user_id` do usuário,
  não por nome de usuário. O deep link do BotFather não carrega `state`.
- Tornar o onboarding idempotente e suportar tentativas paralelas com estados
  explícitos: `pending`, `created`, `configuring`, `active` e `failed`.
- Tratar o limite individual de criação de bots como erro recuperável e
  orientado ao usuário.
- Confirmar no `getMe` do bot gerenciador que `can_manage_bots` está habilitado
  e solicitar `managed_bot` em `allowed_updates`.
- Se a biblioteca Python ainda não modelar os recursos da Bot API usados,
  manter um cliente HTTP pequeno, tipado e coberto por testes de contrato.

## Chat e entrega

- Não inferir `chat_id` a partir do login. O bot do usuário só fica pronto para
  entrega depois que o proprietário envia `/start`, e o `chat_id` é capturado
  dessa atualização.
- Webhooks usam identificador público opaco por bot, segredo próprio e
  deduplicação por `(bot_id, update_id)`.
- Tokens não aparecem em URLs, logs, argumentos de tarefas, filas ou Redis.

## Ensaios obrigatórios

- Criar bot, recuperar token, substituir token, revogar acesso e excluir bot.
- Transferir propriedade quando aplicável e repetir o fluxo após rotação de
  token.
- Medir o comportamento do bot gerenciador com uma pequena frota antes de
  assumir escala; o limite total não está documentado publicamente.
- Nunca rotacionar o token nem configurar webhook no bot atual do MVP durante
  esses ensaios.
