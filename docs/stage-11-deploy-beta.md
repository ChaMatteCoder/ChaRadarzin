# Etapa 11 — deploy e beta

Esta etapa empacota o ChaRadarzin sem remover o modo local nem o MVP legado. O
ambiente Compose contém PostgreSQL, aplicação Gunicorn, worker de coleta,
scheduler idempotente e monitor operacional. O container web executa migrations
e coleta os estáticos antes de aceitar tráfego.

## Matriz de serviços

| Serviço | Responsabilidade | Sinal de saúde |
|---|---|---|
| `db` | PostgreSQL persistente | `pg_isready` |
| `web` | Django/Gunicorn e estáticos WhiteNoise | `/health/ready/` |
| `worker` | fila, coleta e entrega de alertas | jobs/leases no snapshot |
| `scheduler` | cria o job diário idempotente | evento `DAILY_COLLECTION_SCHEDULED` |
| `monitor` | snapshot periódico e falha em estado crítico | restart/status do container |

`/health/live/` confirma somente que o processo web responde. `/health/ready/`
também exige banco acessível e nenhuma migration pendente. Nenhum endpoint expõe
versão, host, credencial ou detalhe de exceção.

## Ambiente local reproduzível

Pré-requisito: Docker Desktop com o comando `docker compose` disponível.

```powershell
Copy-Item .env.compose.example .env.compose
docker run --rm python:3.13-alpine `
  python -c "import secrets; print(secrets.token_urlsafe(48))"
docker compose --env-file .env.compose config
docker compose --env-file .env.compose up --build -d
docker compose --env-file .env.compose ps
(Invoke-WebRequest -UseBasicParsing `
  http://127.0.0.1:8000/health/ready/).Content
```

Preencha `DJANGO_SECRET_KEY` e `POSTGRES_PASSWORD` com valores diferentes e
URL-safe. O perfil local começa sem OIDC e manager para que a instalação limpa
possa subir sem credenciais externas. Se a porta 8000 estiver ocupada pelo
`runserver` local, pare-o ou escolha outro `APP_PORT`.

## Homologação

1. Copie `.env.staging.example` para `.env.staging`.
2. Troque todos os marcadores e configure domínio, origens CSRF, OIDC, manager,
   UUID/segredo de webhook e chave Fernet.
3. Termine TLS em um proxy confiável. Habilite `DJANGO_TRUST_PROXY_HEADERS=sim`
   somente quando o proxy remover qualquer `X-Forwarded-Proto` recebido do
   cliente e criar o header correto.
4. Valide antes de subir:

```powershell
docker compose --env-file .env.staging config
docker compose --env-file .env.staging build
docker compose --env-file .env.staging run --rm `
  -e MIGRATE_ON_STARTUP=nao -e COLLECTSTATIC_ON_STARTUP=nao `
  web python manage.py check --deploy
docker compose --env-file .env.staging up -d
docker compose --env-file .env.staging ps
```

5. Com a URL HTTPS pública acessível, configure o webhook do manager:

```powershell
docker compose --env-file .env.staging exec web `
  python manage.py validate_manager_bot --configure-webhook
```

## Cadastro de usuários beta

Com `BETA_ACCESS_REQUIRED=sim`, somente Telegram User IDs ativos na allowlist
podem concluir o OIDC. O ID é dado pessoal: obtenha-o diretamente do participante
e não o registre em issue, log ou commit.

```powershell
docker compose --env-file .env.staging exec web python manage.py `
  register_beta_user --telegram-user-id 123456789 `
  --username usuario --name "Pessoa Beta"
```

Para revogar novos logins dessa identidade:

```powershell
docker compose --env-file .env.staging exec web python manage.py `
  register_beta_user --telegram-user-id 123456789 --disable
```

O comando é idempotente e não imprime o ID. A concessão registra quando foi
resgatada e qual usuário interno recebeu o acesso.

## Backup diário

O backup usa `pg_dump` custom, não inclui senha na linha de comando e cria um
manifesto adjacente com tamanho e SHA-256. `backups/` não é versionado.

```powershell
docker compose --env-file .env.staging exec web `
  python manage.py backup_database
docker compose --env-file .env.staging exec web `
  python manage.py restore_database backups/ARQUIVO.dump --verify-only
```

Copie o `.dump` e o `.dump.json` juntos para armazenamento externo cifrado,
com retenção definida pelo operador. A presença no volume local não substitui
uma cópia fora do host.

## Restore e exercício de recuperação

Restore substitui o conteúdo do banco. Faça primeiro um backup do estado atual,
registre a imagem implantada e pare os processos que acessam a base:

```powershell
docker compose --env-file .env.staging stop web worker scheduler monitor
docker compose --env-file .env.staging run --rm `
  -e MIGRATE_ON_STARTUP=nao -e COLLECTSTATIC_ON_STARTUP=nao `
  web python manage.py restore_database backups/ARQUIVO.dump --confirm
docker compose --env-file .env.staging up -d
docker compose --env-file .env.staging exec web `
  python manage.py check --deploy
docker compose --env-file .env.staging exec web `
  python manage.py operational_status --json
```

Execute esse exercício em homologação antes do beta e depois periodicamente. A
CI cria um registro sintético, faz backup, remove o registro, restaura o banco e
confirma que o registro voltou.

## Monitoramento

- sondar `/health/live/` e `/health/ready/` externamente;
- alertar quando `web`, `worker`, `scheduler` ou `monitor` reiniciar;
- coletar stdout JSON dos containers;
- executar `operational_status --fail-on critical` em verificações externas;
- acompanhar jobs mortos, leases vencidos, lotes parciais/falhos, webhooks,
  entregas e bots não saudáveis.

## Checklist de deploy

- [ ] CI verde e imagem identificada por tag imutável;
- [ ] `.env.staging` fora do Git e sem marcadores;
- [ ] DNS/TLS, hosts, CSRF e proxy validados;
- [ ] backup novo e `--verify-only` verde;
- [ ] usuário beta operador cadastrado antes de exigir allowlist;
- [ ] `check --deploy` e `makemigrations --check --dry-run` verdes;
- [ ] containers saudáveis e migrations concluídas uma única vez pelo web;
- [ ] login OIDC, bot pessoal, dois tenants e isolamento validados;
- [ ] coleta baseline controlada e alerta simulado idempotente validados;
- [ ] endpoints e snapshot operacional integrados ao monitor externo;
- [ ] imagem anterior e backup pré-deploy disponíveis para rollback.

## Plano de rollback

1. Interrompa novas coletas (`worker` e `scheduler`) e preserve logs.
2. Se a migration for compatível com a versão anterior, aplique a tag anterior
   em `CHADARADZIN_IMAGE_TAG` e recrie web/worker/scheduler/monitor.
3. Se a versão anterior não aceitar o schema novo, pare todos os serviços de
   aplicação e restaure o backup pré-deploy com o procedimento acima.
4. Valide readiness, `check --deploy`, snapshot operacional, login e um produto
   beta antes de reabrir tráfego.
5. Não use `docker compose down -v`; isso remove o volume persistente.

## CI

`.github/workflows/ci.yml` executa Django/PostgreSQL, ambas as suítes, checagem de
migrations, `collectstatic`, compilação, restore real e build da imagem. Nenhum
teste automatizado chama lojas ou Telegram reais.

## Validação nesta máquina

Em 29/08/2026, a instalação local foi validada com Docker Desktop: os cinco
serviços iniciaram, `db` e `web` ficaram saudáveis, e `/health/live/` e
`/health/ready/` responderam `200`. O perfil também foi exercitado com OIDC e
manager habilitados por um Cloudflare Quick Tunnel.

Quick Tunnels são temporários. Após reiniciar o computador ou o processo
`cloudflared`, atualize `PUBLIC_BASE_URL`, `DJANGO_ALLOWED_HOSTS` e
`DJANGO_CSRF_TRUSTED_ORIGINS`, recrie os containers, reconfigure o webhook do
manager e altere o domínio do Login Widget no BotFather. Credenciais e Telegram
User IDs não devem ser compartilhados no chat.

Em 06/09/2026, a validação humana final também foi concluída: login Telegram,
cadastro de produtos, comandos do bot pessoal e notificação administrativa de
teste funcionaram no ambiente Docker. A notificação foi registrada como
`ADMIN_TEST_NOTIFICATION_SENT` e, por projeto, não criou alerta, entrega ou
histórico de preço falsos.
