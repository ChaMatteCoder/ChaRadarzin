# Etapa 3 — bot gerenciador e bot pessoal

Esta etapa adiciona a conexão de um bot pessoal por tenant sem pedir que o
usuário copie um token. O fluxo usa o recurso oficial de managed bots do
Telegram: o bot gerenciador recebe `managed_bot`, o servidor obtém a
credencial com `getManagedBotToken`, restringe o acesso ao proprietário e
configura o webhook do bot filho.

## Dois papéis no Telegram

- O bot de login (`TELEGRAM_OIDC_*`) autentica a conta web.
- O bot gerenciador (`TELEGRAM_MANAGER_*`) cria e entrega bots pessoais.

Eles são integrações distintas. O ID numérico do Telegram é associado a uma
identidade local; o `sub` do OIDC continua sendo tratado como identificador
separado.

## Configuração local

Gere uma chave Fernet e preencha somente o `.env` local:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

```dotenv
PUBLIC_BASE_URL=https://radar.exemplo.com
TELEGRAM_MANAGER_ENABLED=true
TELEGRAM_MANAGER_BOT_TOKEN=<manager-token-from-secret-store>
TELEGRAM_MANAGER_BOT_USERNAME=meu_manager_bot
TELEGRAM_MANAGER_WEBHOOK_ID=6a2f16e0-96bc-4de7-a537-58dbf62f9d34
TELEGRAM_MANAGER_WEBHOOK_SECRET=um-segredo-longo-e-aleatorio
BOT_TOKEN_ENCRYPTION_KEYS=1:CHAVE_FERNET_BASE64
```

Em produção `PUBLIC_BASE_URL` precisa ser HTTPS. O ID do webhook é opaco e não
é o token; o segredo é enviado pelo Telegram no cabeçalho
`X-Telegram-Bot-Api-Secret-Token`. Tokens nunca entram em argumentos de
comandos, HTML, logs, recibos de update ou respostas HTTP.

## BotFather e validação

No @BotFather, habilite Bot Management Mode no bot gerenciador e configure o
username do manager. Depois valide a capacidade e, quando o domínio estiver
publicado, registre o webhook:

```powershell
python manage.py validate_manager_bot
python manage.py validate_manager_bot --configure-webhook
```

O endpoint do manager aceita somente `managed_bot`. Cada bot pessoal recebe um
webhook próprio em `/webhooks/telegram/bot/<uuid>/`, com segredo independente.

## Fluxo do usuário

1. A pessoa abre `/painel/bot/` autenticada com Telegram e informa nome e
   username sugeridos.
2. O link oficial `https://t.me/newbot/<manager>/<username>?name=<nome>` abre o
   fluxo de criação do Telegram.
3. O update `managed_bot` correlaciona o proprietário e a sessão de onboarding;
   a credencial é cifrada com Fernet antes de ser salva.
4. O servidor chama `getMe`, `setMyCommands`, restringe o acesso ao owner e
   registra `setWebhook`.
5. A pessoa envia `/start` ao novo bot. O chat privado é salvo e o status passa
   para **Conectado**.

O bot responde apenas ao `from.id` do proprietário em chat privado. O conjunto
inicial de comandos é `/start`, `/produtos`, `/status`, `/pausar`,
`/configurar` e `/atualizar`; este último cria uma solicitação com cooldown,
enfileira uma coleta manual e acompanha seu resultado no worker.

## Revogação e fallback

`my_chat_member` marca o bot como bloqueado quando o owner o remove. O comando
`check_managed_bots` verifica `getMe` e `getWebhookInfo`, marca revogação HTTP
401, limpa a credencial local e detecta webhook divergente:

```powershell
python manage.py check_managed_bots
python manage.py rotate_managed_bot_token --tenant UUID_DO_TENANT
python manage.py expire_bot_onboarding
```

Enquanto o manager não estiver disponível, o fallback temporário aceita token
somente por variável de ambiente (nunca pela linha de comando):

```powershell
$env:TELEGRAM_FALLBACK_BOT_TOKEN = "..."
python manage.py attach_fallback_bot --tenant UUID_DO_TENANT
```

O fallback mantém a mesma autenticação de webhook e a mesma restrição de owner,
mas não substitui a entrega oficial de managed bots.

## Validação

Os testes usam sessões HTTP simuladas e clientes Telegram falsos. Cobrem
criptografia, idempotência por `update_id`, segredo do webhook, onboarding,
associação, rotação, `/start`, cooldown, isolamento do tenant, bloqueio e
rejeição de usuário não proprietário. A aceitação real depende de um manager
habilitado no BotFather e de um domínio HTTPS acessível; nenhum teste local
envia token ou chama o Telegram.
