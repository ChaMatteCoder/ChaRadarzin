# ChaRadarzin

> Radar local de preços que compara o custo realmente pago, preserva o
> histórico e avisa somente quando aparece uma oportunidade relevante.

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-2563EB)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-D97706.svg)](LICENSE)

O **ChaRadarzin** nasceu como um protótipo funcional para responder uma pergunta
simples: _qual loja oferece o menor custo total para o produto exato que eu
quero comprar?_ Em vez de comparar apenas o valor anunciado, o radar valida o
produto, soma o frete para o CEP configurado localmente, registra o histórico e
reduz o resultado a uma melhor oferta confiável.

Este repositório é a base do projeto final ChaRadarzin. O MVP já percorre todo o
fluxo: cadastro em Excel, coleta real, comparação, histórico, automação diária,
alertas no Telegram e análises conservadoras.

## O que o MVP faz

- lê e valida produtos e links cadastrados em `produtos.xlsx`;
- coleta preços da Amazon Brasil e da KaBuM por adaptadores independentes;
- valida domínio, modelo exato, variante, vendedor, estoque e pagamento;
- consulta frete para um CEP mantido exclusivamente no `.env` local;
- escolhe a melhor oferta pelo total **produto + frete**;
- armazena execuções e observações em SQLite;
- gera relatório Markdown com diagnóstico dos links;
- executa ao entrar no Windows e mantém uma verificação diária de segurança;
- envia pelo Telegram somente alertas relevantes nas execuções automáticas;
- calcula estatísticas, tendência, volatilidade, sazonalidade e previsão somente
  quando existe histórico suficiente.

## Como funciona

```text
produtos.xlsx
     │
     ▼
validação do catálogo
     │
     ▼
coletores Amazon / KaBuM ──► consulta de frete
     │
     ▼
ofertas válidas ──► melhor custo total
     │                    │
     ▼                    ├──► relatório Markdown e gráfico
SQLite                    └──► regras de alerta ──► Telegram
     │
     └──► histórico e análises
```

Uma falha em uma loja não derruba a coleta inteira. O problema é registrado no
diagnóstico, mas anúncios com modelo, vendedor, pagamento ou frete inválidos não
entram na comparação.

## Requisitos

- Docker Desktop com Docker Compose para a instalação reproduzível recomendada;
- ou Python 3.11 ou superior para desenvolvimento sem contêiner;
- Windows 10 ou 11 somente para a automação local legada incluída;
- Microsoft Excel ou outro editor compatível com `.xlsx` para editar o catálogo;
- conexão com a internet durante as coletas reais;
- bot do Telegram, opcional, para receber alertas.

## Instalação

### Instalação limpa com Docker Compose

Este é o caminho recomendado para executar a aplicação web completa com
PostgreSQL, migrações automáticas, web, worker, scheduler e monitor:

```powershell
git clone https://github.com/ChaMatteCoder/ChaRadarzin.git
cd ChaRadarzin
Copy-Item .env.compose.example .env.compose
docker run --rm python:3.13-alpine `
  python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Use duas saídas diferentes do último comando para substituir
`DJANGO_SECRET_KEY` e `POSTGRES_PASSWORD` em `.env.compose`. Depois valide e
suba o ambiente:

```powershell
docker compose --env-file .env.compose config
docker compose --env-file .env.compose up --build -d
docker compose --env-file .env.compose ps
(Invoke-WebRequest -UseBasicParsing `
  http://127.0.0.1:8000/health/ready/).Content
```

Abra `http://127.0.0.1:8000/`. O primeiro startup executa migrations e
`collectstatic`; o login Telegram permanece desativado no perfil local até as
credenciais serem configuradas. Se a porta 8000 já estiver em uso, troque
`APP_PORT` em `.env.compose`. Para parar sem apagar o banco:

```powershell
docker compose --env-file .env.compose stop
```

Não use `down -v` em um ambiente com dados: a opção remove o volume do
PostgreSQL. Homologação, cadastro beta, backup, restore e rollback estão no
[runbook da Etapa 11](docs/stage-11-deploy-beta.md).

### Instalação local com Python

```powershell
git clone https://github.com/ChaMatteCoder/ChaRadarzin.git
cd ChaRadarzin
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edite o `.env` localmente. Nunca envie esse arquivo ao Git:

```dotenv
CEP_ENTREGA=00000000
RADAR_TIMEOUT_SEGUNDOS=20
RADAR_TENTATIVAS=1
TELEGRAM_NOTIFICACOES=nao
TELEGRAM_ALERTAR_AUMENTO=nao
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

O número do imóvel não é necessário para a cotação. O CEP só é enviado às lojas
quando a execução usa `--with-shipping`.

## Catálogo de produtos

A planilha possui duas abas obrigatórias.

### Produtos

| Coluna | Descrição |
|---|---|
| `produto_id` | Identificador único usado em todas as abas |
| `ativo` | Define se o produto participa da coleta |
| `produto` | Nome amigável |
| `modelo_exato` | Código que precisa aparecer no anúncio |
| `preco_alvo` | Valor máximo desejado |
| `pagamento` | Forma de pagamento esperada, como `PIX` |

### Links

| Coluna | Descrição |
|---|---|
| `produto_id` | Referência ao produto |
| `loja` | Loja declarada |
| `url` | URL exata do anúncio |
| `vendedor_esperado` | Vendedor que deve ser validado |
| `variante` | Capacidade, tamanho ou outra variante esperada |

Produtos ativos precisam ter pelo menos um link. Se a planilha estiver inválida,
a execução termina sem inventar dados.

## Executando

Coleta real sem frete:

```powershell
python -m app.main
```

Coleta real com comparação de produto e frete:

```powershell
python -m app.main --with-shipping
```

Somente um produto:

```powershell
python -m app.main --product SSD001 --with-shipping
```

Validação com preços simulados:

```powershell
python -m app.main --simulate
```

O relatório mais recente é criado em `reports/ultimo_relatorio.md`. Bancos,
logs e relatórios reais são artefatos locais e estão ignorados pelo Git.

## Fundação web multiusuário

O CLI do MVP permanece disponível. A fundação web Django usa outro banco e
começa sem permitir escrita de produtos pelo painel:

```powershell
python manage.py migrate
python manage.py runserver
```

Abra `http://127.0.0.1:8000/`. Em desenvolvimento, sem as credenciais OIDC, a
página inicial informa que o login Telegram ainda está em configuração. Para
habilitá-lo, preencha no `.env` as variáveis `TELEGRAM_OIDC_*` e mantenha os
segredos somente no ambiente local.

Sem `DATABASE_URL`, o desenvolvimento usa `data/chadaradzin_web.sqlite3`. O
ambiente `CHADARADZIN_ENV=production` recusa iniciar sem `DJANGO_SECRET_KEY` e
uma `DATABASE_URL` PostgreSQL.

### Produtos e painel (Etapa 4)

Depois do login, `/painel/` permite cadastrar um produto por link exato da
Amazon Brasil ou da KaBuM, revisar a prévia extraída e confirmar manualmente
modelo, variante, vendedor, pagamento e preço-alvo. Também é possível adicionar
outras fontes ao mesmo produto, editar, pausar, arquivar e configurar CEP e
preferência de pagamento.

O HTML da loja não é armazenado. A prévia usa uma lista fechada de hosts,
validação de DNS público, bloqueio de redirecionamento, limite de tamanho,
timeout, expiração e rate limit por tenant. Consulte
[docs/stage-4-products-panel.md](docs/stage-4-products-panel.md) para o fluxo,
as garantias e a validação humana necessária antes de produção.

Produtos confirmados aparecem como **Aguardando primeira coleta**. O motor da
Etapa 5 coleta cada URL canônica uma vez por lote, calcula frete de forma
individual e cria a baseline na primeira observação válida. Confirmar um
produto não dispara alerta.

### Motor compartilhado de coleta (Etapa 5)

O mesmo link acompanhado por vários tenants reutiliza preço-base, título,
vendedor e estoque. CEP, pagamento, frete, prazo, total, validações e baseline
continuam privados. Para executar um lote manual:

```powershell
py -3.14 manage.py collect_active_offers
```

O HTML das lojas não é persistido, falhas são isoladas e a coleta respeita um
orçamento configurável de requisições por loja. Scheduler, filas e retries com
backoff estão implementados na Etapa 6. Consulte
[docs/stage-5-collection-engine.md](docs/stage-5-collection-engine.md).

### Agendamento e workers (Etapa 6)

O scheduler registra um job diário idempotente no banco e um worker separado
executa o motor compartilhado. Tentativas, duração, lote, backoff e falhas
esgotadas ficam rastreáveis sem registrar CEP ou credenciais:

```powershell
py -3.14 manage.py migrate
py -3.14 manage.py schedule_daily_collection --force
py -3.14 manage.py run_collection_worker --once
```

Em servidor, execute `run_collection_worker` continuamente. No Windows, os
scripts de automação chamam scheduler e worker de uma vez. Consulte
[docs/stage-6-scheduler-workers.md](docs/stage-6-scheduler-workers.md).

### Motor de alertas (Etapa 7)

Ao terminar cada lote, o sistema escolhe a melhor oferta válida por produto e
cria eventos privados para reduções relevantes, novo menor histórico, preço-alvo
ou retorno ao estoque. Baselines não alertam, oscilações pequenas são apenas
registradas e cooldown/idempotência evitam repetição. Consulte
[docs/stage-7-alert-engine.md](docs/stage-7-alert-engine.md).

### Notificações e ações rápidas (Etapa 8)

O worker entrega eventos pelo bot pessoal correto, registra `SENT`, `FAILED` ou
`BLOCKED` e nunca repete a mesma entrega bem-sucedida. A mensagem inclui preço,
frete, prazo, total, histórico, motivo e botões para abrir a oferta, o histórico,
o painel ou pausar aquele produto. Os comandos continuam restritos ao
proprietário; `/atualizar` cria um job manual com cooldown e acompanha seu
resultado.

Para reprocessar entregas pendentes:

```powershell
py -3.14 manage.py deliver_pending_alerts --limit 100
```

Consulte [docs/stage-8-notifications.md](docs/stage-8-notifications.md) para os
estados, falhas seguras e a validação humana necessária antes de enviar uma
mensagem real.

### Histórico e experiência (Etapa 9)

Cada produto apresenta a melhor oferta válida atual, preço atual, menor histórico,
variação diária, baseline, status da coleta, eventos de alerta e estado do bot.
O histórico diário tem gráfico acessível e lista dos dias recentes; quando ainda
não há coleta válida, o painel explica o motivo em vez de exibir números vazios.
Consulte [docs/stage-9-history-experience.md](docs/stage-9-history-experience.md).

Valide uma base legada sem gravar no banco novo:

```powershell
python manage.py import_legacy_sqlite `
  --source data/radar_precos.db `
  --dry-run
```

Para gravar, remova `--dry-run` e informe `--tenant UUID_DO_TENANT` somente após
conferir tenant, backup, hash e contagens. O
importador abre a origem em modo somente leitura, é idempotente pelo hash e não
remove nem converte o SQLite original.

## Alertas do Telegram

### Bot pessoal (Etapa 3)

O painel `/painel/bot/` permite criar um bot pessoal pelo fluxo oficial de
managed bots, sem copiar token. O servidor cifra a credencial, restringe o bot
ao proprietário e registra um webhook com segredo próprio. Consulte
[docs/stage-3-managed-bots.md](docs/stage-3-managed-bots.md) para configurar o
bot gerenciador, validar a capacidade e executar os comandos de saúde/rotação.

O manager e o bot de login OIDC são papéis separados. O fallback temporário só
aceita credencial via variável de ambiente e nunca por argumento de CLI.

Após criar um bot pelo `@BotFather`, envie `/start` para ele, configure o token e
o Chat ID somente no `.env` e valide:

```powershell
python -m app.main --test-telegram
```

Com `TELEGRAM_NOTIFICACOES=sim`, o radar consulta e registra sempre, mas envia no
máximo uma mensagem por produto quando ocorrer pelo menos uma destas condições:

- queda de 1% ou mais desde a última consulta;
- novo menor preço histórico;
- preço-alvo atingido;
- retorno ao estoque;
- aumento de 5% ou mais, apenas se `TELEGRAM_ALERTAR_AUMENTO=sim`.

Oscilações menores ficam somente no SQLite. Quando várias regras são acionadas,
os motivos são consolidados na mensagem da melhor oferta; não há um alerta por
loja.

O resumo operacional é independente dos alertas de preço e pode ser solicitado
manualmente com `--notify-summary`. O Agendador diário não usa essa opção: sem
uma mudança relevante, nenhuma mensagem é enviada.

## Automação no Windows

Valide os caminhos primeiro:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts\run_scheduled.ps1 -ValidateOnly
```

Visualize a configuração sem registrar a tarefa:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts\install_scheduled_task.ps1 -DailyAt 21:05 -Preview
```

Registre a tarefa:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts\install_scheduled_task.ps1 -DailyAt 21:05
```

A tarefa espera 2 minutos após o login para a rede estabilizar e também mantém
uma execução diária às 21:05 caso o computador já esteja ligado. Ela aguarda a
rede, executa assim que possível quando um horário for perdido, desperta o
computador quando permitido pelo Windows e tenta novamente até três vezes. A
fila persistida aplica idempotência diária, backoff e exclusão mútua entre
workers.

O arquivo `logs/scheduler.log` registra somente estados operacionais e códigos
de saída, sem armazenar CEP ou credenciais. O banco registra o job e cada
tentativa para auditoria.

## Análises e previsões

O radar usa uma observação por dia: a melhor oferta da execução mais recente
daquele dia. Coletas com frete, sem frete e simuladas nunca são misturadas.

| Recurso | Critério |
|---|---|
| Menor, maior, média e mediana | Disponível desde o primeiro preço válido |
| Média móvel de 7 dias | 7 dias válidos |
| Média móvel de 30 dias | 30 dias válidos |
| Tendência linear | 7 dias válidos |
| Gráfico SVG | 8 dias válidos |
| Promoção atípica | 10% abaixo da mediana de ao menos 7 dias anteriores |
| Volatilidade por loja | Coeficiente de variação com ao menos 2 dias por loja |
| PIX versus cartão | Histórico válido nas duas formas de pagamento |
| Black Friday | Duas temporadas com amostras antes e durante o evento |
| Previsão de 7 dias | 60 dias coletados em uma janela mínima de 90 dias |

Quando a amostra é pequena, o relatório mostra **dados insuficientes** em vez de
produzir uma previsão frágil. A previsão liberada continua sendo uma extrapolação
de tendência, não uma garantia de preço futuro.

## Estrutura do projeto

```text
app/
  collectors/       # coleta e parsing por loja
  notifications/    # cliente e mensagens do Telegram
  address.py        # validação do CEP e ViaCEP
  alerts.py         # regras que evitam spam
  analytics.py      # métricas, tendência, sazonalidade e gráfico
  comparison.py     # escolha da melhor oferta
  config.py         # configuração local
  database.py       # persistência SQLite
  main.py           # pipeline e CLI
  report.py         # relatório Markdown
  shipping.py       # cotações de frete
  spreadsheet.py    # leitura e validação do Excel
scripts/            # automação do Windows
tests/              # testes unitários e de integração isolada
```

## Testes

```powershell
py -m unittest discover -s tests -v
py -m compileall -q app tests
python manage.py test tenancy monitoring
python manage.py check
python manage.py makemigrations --check --dry-run
```

Os testes usam respostas estáticas ou objetos simulados. Eles não dependem de
tokens reais e não devem chamar lojas ou Telegram.

O gate de qualidade e segurança da Etapa 10, incluindo a jornada end-to-end
controlada, o threat model e o comando de métricas/alertas, está descrito em
[docs/stage-10-quality-security.md](docs/stage-10-quality-security.md).

Para obter um snapshot operacional seguro:

```powershell
py -3.14 manage.py operational_status --json
```

O pacote de deploy da Etapa 11 inclui Compose, healthchecks, CI, backup com
SHA-256, restore testado, homologação fechada e rollback. Consulte
[docs/stage-11-deploy-beta.md](docs/stage-11-deploy-beta.md).

Backup diário, retenção, restauração e resposta a incidentes estão detalhados
em [docs/operations-recovery.md](docs/operations-recovery.md).

## Segurança e privacidade

- `.env`, SQLite, logs, relatórios e temporários do Office são ignorados;
- tokens e Chat IDs não aparecem em logs, relatórios ou representação da configuração;
- a sessão de localização da Amazon usa cookies descartáveis somente em memória;
- URLs, modelo, variante e vendedor são validados antes de aceitar uma oferta;
- falhas de rede, parsing ou frete não viram preços válidos;
- contribuições devem usar dados sintéticos em fixtures.

A política pública está em `/privacidade/`; pessoas autenticadas podem excluir
conta e dados locais em `/conta/excluir/`. A exclusão exige a frase de
confirmação exibida na tela.

Consulte [SECURITY.md](SECURITY.md) antes de reportar uma vulnerabilidade.

## Limitações do protótipo

- integra apenas Amazon Brasil e KaBuM;
- mudanças no HTML ou nas APIs das lojas podem exigir manutenção dos coletores;
- o computador precisa estar ligado para executar a tarefa, embora o Windows
  possa recuperar uma execução perdida;
- coleta automatizada deve respeitar os termos e limites aplicáveis de cada loja;
- o MVP não compra produtos e não acessa contas das lojas.

## Próximos passos do ChaRadarzin

- adicionar lojas por plugins de coletores;
- permitir regras de alerta por produto;
- disponibilizar uma interface local para editar o catálogo;
- ampliar análises quando houver histórico real suficiente;
- empacotar instalação, atualização e diagnóstico.

## Contribuição e licença

Leia [CONTRIBUTING.md](CONTRIBUTING.md) para colaborar. O projeto é distribuído
sob a [licença MIT](LICENSE).
