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
- envia um resumo das execuções automáticas e alertas relevantes pelo Telegram;
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

- Windows 10 ou 11 para a automação incluída;
- Python 3.11 ou superior;
- Microsoft Excel ou outro editor compatível com `.xlsx` para editar o catálogo;
- conexão com a internet durante as coletas reais;
- bot do Telegram, opcional, para receber alertas.

## Instalação

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
FORMA_PAGAMENTO=PIX
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

## Alertas do Telegram

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

As execuções iniciadas pelo Agendador também enviam um resumo curto com a melhor
oferta válida de cada produto. Esse resumo operacional é independente dos
alertas de preço e pode ser solicitado manualmente com `--notify-summary`.

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
computador quando permitido pelo Windows, tenta novamente até três vezes e
impede instâncias simultâneas.

O arquivo `logs/scheduler.log` registra início, término e código de saída sem
armazenar CEP ou credenciais. Execuções repetidas em menos de seis horas são
ignoradas para evitar coleta e mensagem duplicadas.

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
```

Os testes usam respostas estáticas ou objetos simulados. Eles não dependem de
tokens reais e não devem chamar lojas ou Telegram.

## Segurança e privacidade

- `.env`, SQLite, logs, relatórios e temporários do Office são ignorados;
- tokens e Chat IDs não aparecem em logs, relatórios ou representação da configuração;
- a sessão de localização da Amazon usa cookies descartáveis somente em memória;
- URLs, modelo, variante e vendedor são validados antes de aceitar uma oferta;
- falhas de rede, parsing ou frete não viram preços válidos;
- contribuições devem usar dados sintéticos em fixtures.

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
