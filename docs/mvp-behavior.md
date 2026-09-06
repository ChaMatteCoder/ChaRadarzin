# Contrato protegido do MVP

Este documento registra o comportamento que deve permanecer estável durante a
evolução do ChaRadarzin para o produto multiusuário.

## Entrada e coleta

- O catálogo versionado fica em `produtos.xlsx`, com as abas `Produtos` e
  `Links`.
- Somente links exatos de lojas suportadas são coletados.
- Modelo, variante, vendedor, estoque e pagamento são validados antes de uma
  oferta entrar na comparação.
- Falha de rede, parsing ou frete é registrada como diagnóstico e nunca vira
  indisponibilidade ou preço válido.
- O custo comparável é o preço do produto somado ao frete quando a execução
  exige frete.

## Histórico e alertas

- A primeira oferta válida cria a baseline e não gera evento de preço.
- Queda de pelo menos 1%, novo menor histórico, cruzamento do preço-alvo e
  retorno ao estoque são eventos relevantes.
- Aumento de preço permanece desativado por padrão.
- Pequenas oscilações são registradas sem notificação.
- Falhas de coleta são ignoradas ao procurar o último preço ou estado de
  estoque comparável.
- Apenas observações de execuções concluídas com sucesso entram no histórico;
  a execução corrente pode ser incluída explicitamente enquanto o relatório é
  montado.
- O agendador diário não envia resumo operacional. `--notify-summary` continua
  disponível somente para uso manual e explícito.

## Dados e privacidade

- O CEP e as credenciais ficam somente no `.env` local.
- Endereço derivado, CEP, token e Chat ID não podem aparecer em logs,
  relatórios ou representações de configuração.
- O banco SQLite, logs, relatórios e arquivos locais de execução não são
  versionados.

## Verificação

O contrato é protegido pela suíte em `tests/`, por fixtures sintéticas e pela
validação do executor agendado. Testes automatizados não acessam lojas reais nem
o Telegram.
