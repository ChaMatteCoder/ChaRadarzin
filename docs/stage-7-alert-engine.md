# Etapa 7 — motor de alertas

O motor de alertas avalia a melhor observação válida de cada produto ao final
de um lote da Etapa 5. Ele não envia Telegram: cria eventos privados por tenant
para a Etapa 8 consumir.

## Regras

Por padrão, uma observação válida pode gerar um único evento consolidado quando:

- o custo total caiu pelo menos o percentual mínimo configurado;
- atingiu o preço-alvo na transição de acima para igual ou abaixo;
- tornou-se um novo menor histórico com queda relevante;
- voltou ao estoque depois de uma observação anterior explicitamente sem estoque;
- aumentou pelo menos 5%, somente quando o usuário habilitou aumentos.

A primeira observação marcada como baseline nunca gera variação. Oscilações
menores são salvas como histórico, mas não criam evento. Quando mais de uma regra
é atendida, os tipos são ordenados e consolidados em um único `event_type`, por
exemplo `NEW_HISTORICAL_LOW|TARGET_REACHED|PRICE_DROP`.

## Idempotência e cooldown

`AlertEvent.idempotency_key` combina tenant, produto, observação e conjunto de
regras. Reavaliar a mesma observação retorna o mesmo evento e não cria uma linha
duplicada. Antes de criar um novo evento, o motor verifica o último evento do
produto dentro do cooldown configurado (padrão: 360 minutos). O cooldown impede
spam entre quedas consecutivas; não apaga nem altera observações de preço.

As informações de frete, pagamento, preço-alvo, histórico e eventos permanecem
no tenant. Nenhum evento é criado para falha de coleta, página sem preço ou
produto indisponível sem uma transição explícita de retorno ao estoque.

## Persistência

- `AlertRule`: regra privada do produto, herdando inicialmente o percentual e a
  preferência de aumento do perfil de entrega;
- `AlertEvent`: evento criado, observação de origem, preços, variação, motivo e
  chave idempotente;
- `NotificationDelivery`: a estrutura é consumida pela entrega privada da
  Etapa 8, mantendo o evento separado do resultado de envio.

Ao alterar as preferências do radar no painel, as regras existentes do tenant
recebem o novo percentual mínimo e a opção de aumento.

## Validação

Os testes cobrem baseline sem alerta, redução de 1%, oscilação irrelevante,
menor histórico, preço-alvo, retorno ao estoque, limiar vindo do perfil,
cooldown, escolha da melhor oferta e idempotência. Eles usam observações e
fixtures sintéticas; nenhuma loja ou Telegram é acessado.

Uma validação humana de produção continua necessária antes da Etapa 8: executar
um lote controlado com links finais e confirmar que o evento corresponde ao
preço, estoque, frete e pagamento exibidos no painel. Não é necessário enviar
mensagem real durante a validação desta etapa.
