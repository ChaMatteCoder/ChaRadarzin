# Estratégia reversível de migração

A migração para Django e PostgreSQL será aditiva. O SQLite e a planilha do MVP
nunca serão convertidos ou apagados no lugar.

## Preparação

1. Registrar o commit de origem, os hashes de `produtos.xlsx` e do SQLite e as
   contagens por tabela.
2. Copiar os artefatos legados para um backup somente leitura fora do diretório
   usado pela aplicação nova.
3. Criar no PostgreSQL um tenant legado para o proprietário atual.
4. Executar a importação por um `legacy_import_batch` idempotente, identificado
   pelos hashes de origem.
5. Reconciliar produtos, links, execuções e observações por contagem e soma de
   controle, sem enviar notificações.
6. Gravar um cursor de alertas no instante do corte. O histórico importado serve
   para comparação, mas nunca pode gerar alertas retroativos.

## Transição

- O CLI atual permanece executável durante a fundação multiusuário.
- O pipeline novo começa em modo sombra, com webhooks e notificações de preço
  desativados.
- Resultados das duas implementações são comparados usando as mesmas fixtures.
- O corte ocorre por configuração explícita, nunca por remoção do caminho
  legado.
- Tokens de bots são resolvidos no trabalhador a partir de uma referência
  opaca. Eles não entram em argumentos do Celery, filas ou valores do Redis.
- Atualizações recebidas por webhook são idempotentes por `(bot_id, update_id)`.

## Rollback

Em caso de divergência, o scheduler novo é pausado e o CLI atual volta a ser o
executor principal, usando os artefatos originais. Dados criados no PostgreSQL
permanecem preservados para diagnóstico, mas não são copiados de volta para o
SQLite automaticamente.

## Critérios antes do corte

- Importação repetível sem duplicar dados.
- Contagens reconciliadas e amostras de preços conferidas.
- Nenhuma notificação emitida pelo modo sombra.
- Cursor de alertas impede notificações retroativas após a importação.
- Backup restaurado em teste.
- Procedimento de rollback executado em homologação.
