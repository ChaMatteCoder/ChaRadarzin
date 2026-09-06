# Etapa 5 — motor compartilhado de coleta

Esta etapa conecta os produtos do painel aos parsers e provedores de frete já
validados pelo MVP. Um lote coleta cada URL canônica uma única vez e deriva
observações privadas para todos os tenants interessados naquela oferta.

## Separação de dados

`SharedOffer` guarda somente loja e URL canônica pública.
`SharedOfferObservation` guarda o resultado comum da página: título, vendedor,
preço-base, estoque, suporte a Pix, versão do parser e código seguro de erro.
O HTML é mantido apenas em memória durante o lote e nunca é persistido.

`PriceObservation` continua pertencendo a um tenant. Ela associa a fonte e o
produto privados ao resultado comum e acrescenta pagamento, frete, prazo, custo
total, status de validação e indicação de baseline. CEP e preferências nunca
entram nas tabelas compartilhadas.

```text
URL canônica ── 1 fetch ──> observação-base compartilhada
                                  │
                    ┌─────────────┴─────────────┐
                    ▼                           ▼
              tenant A                    tenant B
         validação + CEP + frete      validação + CEP + frete
                    │                           │
                    ▼                           ▼
          observação privada A        observação privada B
```

## Execução de um lote

1. Seleciona somente produtos e fontes ativos.
2. Revalida e normaliza cada URL exata; uma URL insegura não chega à rede.
3. Associa URLs equivalentes à mesma oferta compartilhada.
4. Aplica limite de requisições por loja e baixa cada página uma vez.
5. Executa o parser Amazon ou KaBuM e persiste somente os campos extraídos.
6. Para cada fonte privada, valida modelo, variante, vendedor, estoque e
   pagamento.
7. Calcula frete e prazo com o CEP daquele tenant.
8. Persiste observação, run e erro operacional sem vazar dados de outro tenant.
9. A primeira oferta válida de cada produto cria a baseline pelo menor custo
   total do lote. Nenhum alerta é criado nesta etapa.

Uma falha de uma loja ou de um tenant deixa o lote como `PARTIAL`, mas não
impede que outras ofertas válidas sejam registradas. Falta de estoque é um
estado de negócio, não um erro do coletor.

Quando a página identifica explicitamente que o preço extraído é exclusivo de
Pix, produtos configurados para cartão ou boleto falham de forma fechada com
`PAYMENT_MISMATCH`; o motor não reaproveita um desconto Pix como preço de outra
forma de pagamento.

## Execução manual

Depois das migrações:

```powershell
py -3.14 manage.py migrate
py -3.14 manage.py collect_active_offers
```

Para uma validação operacional pequena:

```powershell
py -3.14 manage.py collect_active_offers --limit 2
```

O comando executa apenas um lote. Scheduler, concorrência distribuída, retries
com backoff e tratamento de fila pertencem à Etapa 6.

## Limites de rede

- `COLLECTION_REQUESTS_PER_MINUTE`: orçamento por loja para páginas e cotações;
- `COLLECTION_TIMEOUT_SECONDS`: timeout da página-base;
- `COLLECTION_MAX_BYTES`: limite do HTML descompactado;
- `COLLECTION_SHIPPING_TIMEOUT_SECONDS`: timeout da cotação individual.

Redirecionamentos, hosts não permitidos, DNS privado e respostas grandes
continuam bloqueados pelo fetcher seguro da Etapa 4. Testes automatizados usam
fixtures e não acessam lojas reais.

## Gate humano antes de produção

Execute um lote controlado com um link final real de cada loja e um CEP de teste
autorizado. Confirme no painel preço-base, vendedor, estoque, frete, prazo,
custo total e baseline. Mudanças de HTML, anti-bot e APIs de frete não podem ser
certificadas exclusivamente por fixtures. Não são necessários token, login de
loja, cartão ou endereço completo.
