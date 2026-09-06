# Etapa 10 — qualidade e segurança

Esta etapa transforma os controles construídos nas fases anteriores em um gate
repetível. Todos os testes de coleta usam fixtures locais e todos os clientes do
Telegram são simulados; a suíte automatizada não acessa lojas nem envia
mensagens reais.

## Controles verificados

| Área | Evidência |
|---|---|
| Unitários | parsers, cálculos, alertas, histórico, formatter JSON e rate limiter |
| Integração | banco, filas, leases, webhooks, criptografia e entrega idempotente |
| End-to-end controlado | preferências → link → confirmação → baseline → queda → alerta → histórico |
| Multiusuário | listas, detalhes e mutações recusam objetos de outro tenant |
| Webhooks | UUID opaco, header secreto, limite de 1 MiB, recibo único e retry seguro |
| SSRF | apenas HTTPS/443, Amazon Brasil ou KaBuM, DNS público, sem redirects e com limite de bytes |
| Secrets | `.env` e dados locais ignorados; tokens cifrados e omitidos do admin e dos logs |
| Rate limiting | prévias por tenant, coleta por loja, `/atualizar` por tenant e webhooks por endpoint |
| Observabilidade | logs JSON com allowlist de campos, snapshot de métricas e alertas por severidade |

## Métricas e alertas operacionais

O comando abaixo não mostra CEP, URLs, Telegram User ID, chat ID, token ou
segredo. Ele retorna somente timestamps, contagens e códigos operacionais:

```powershell
py -3.14 manage.py operational_status --json
```

Para uma tarefa agendada ou monitor externo, use um limite explícito:

```powershell
py -3.14 manage.py operational_status --fail-on critical
```

O processo retorna código diferente de zero quando existem jobs esgotados ou
jobs em execução com lease expirado. Com `--fail-on warning`, também sinaliza
ausência/atraso de coleta, falhas recentes de lote, webhook ou entrega e bots em
estado de erro. Lotes parciais contam como execução concluída, mas geram aviso
operacional enquanto estiverem dentro da janela observada.

O cache local é suficiente para a instalação de um processo desta versão. Um
deploy horizontal deve apontar o cache Django para um backend compartilhado,
para que o limite de webhook seja global entre instâncias.

## Logs estruturados

`STRUCTURED_LOGS=sim` é o padrão. Cada linha contém `timestamp`, `level`,
`logger`, `event` e somente metadados previamente permitidos, como `event_code`,
`status`, `duration_ms`, `job_id` e `batch_id`. Campos livres, CEP, chat ID e
tokens não são copiados para o JSON.

## Threat model resumido

Os ativos protegidos são sessões OIDC, isolamento dos tenants, CEPs, histórico,
integridade das coletas/alertas e credenciais de bots. As principais fronteiras
são navegador → Django, Telegram → webhooks, URL do usuário → rede externa,
banco → Telegram Bot API e scheduler → workers/lojas.

Um atacante remoto não começa com sessão, segredo de webhook ou token. Um
usuário autenticado controla seus próprios formulários e links, mas não ganha
autoridade sobre outro tenant. Conteúdo HTML de loja é tratado como não
confiável. Operadores, admins e variáveis de ambiente permanecem uma fronteira
privilegiada.

A auditoria estática padrão da Etapa 10 concluiu os seis domínios de revisão
sem vulnerabilidades confirmadas. O resultado foi produzido offline; ele não
atesta a configuração de uma futura infraestrutura de produção.

## Gate automatizado

```powershell
py -3.14 manage.py check
py -3.14 manage.py makemigrations --check --dry-run
py -3.14 manage.py test tenancy monitoring telegram_bots
py -3.14 -m unittest discover -s tests -v
py -3.14 -m compileall -q app chadaradzin monitoring telegram_bots tenancy tests
```

## Validação humana antes da Etapa 11

1. Recarregue o painel e confirme que os dois SSDs e o bot conectado continuam
   visíveis.
2. Execute `operational_status --json`; na instalação local, um aviso sobre
   ausência de coleta recente só é esperado se ainda não houve um lote bem-sucedido.
3. Não use o botão de atualização nem envie comandos ao bot durante esse gate;
   a validação visual não precisa gerar tráfego externo.
