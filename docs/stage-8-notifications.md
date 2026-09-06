# Etapa 8 — notificações e ações rápidas

A Etapa 8 transforma `AlertEvent` em uma entrega privada pelo bot pessoal do
tenant. A coleta continua compartilhada, mas a escolha do bot, do chat e dos
produtos é sempre feita pelo `tenant_id` do evento.

## Notificação administrativa de teste

O teste é enviado diretamente ao bot pessoal e não cria `AlertEvent`,
`NotificationDelivery`, observação ou histórico de preço:

```powershell
docker compose --env-file .env.compose exec -T web `
  python manage.py send_test_notification --bot-username Chazinbot --confirm
```

A mensagem é identificada como teste e inclui o botão **Abrir painel**. O
comando não imprime token ou chat ID.

## Entrega

`telegram_bots.delivery.deliver_alert_event()` reserva uma única linha de
`NotificationDelivery` por evento e canal. A reserva transacional, a chave de
entrega e a janela de lease evitam duas mensagens quando workers concorrem ou
quando o mesmo lote é reprocessado. Os estados persistidos são `SENDING`,
`SENT`, `FAILED` e `BLOCKED`.

Uma mensagem informa produto, motivo, loja, vendedor, preço anterior e atual,
frete, total, prazo, menor histórico, preço-alvo e link exato da oferta. Os
botões abrem a oferta validada, o histórico, o painel e permitem pausar aquele
produto. URLs que não passarem pela validação de anúncio exato não são
colocadas no botão.

Quando o bot ainda está aguardando `/start`, foi bloqueado ou não possui uma
credencial utilizável, a entrega é registrada como `BLOCKED` sem chamada
externa. Erros 401 revogam e limpam a credencial; erros 403 marcam o bot como
bloqueado; falhas de transporte ficam como `FAILED` para uma tentativa futura.

## Comandos e fila

`/produtos`, `/status`, `/pausar`, `/configurar` e `/atualizar` continuam sendo
determinísticos e restritos ao proprietário em chat privado. `/atualizar` cria
um `CollectionJob` do tipo `MANUAL_COLLECTION`, ligado a
`ManualRefreshRequest`, e respeita o cooldown configurado em
`TELEGRAM_MANUAL_REFRESH_COOLDOWN_MINUTES`. O worker move a solicitação por
`PENDING → PROCESSING → COMPLETED` (ou `FAILED`).

`TELEGRAM_DELIVERY_LEASE_SECONDS` (padrão `300`) define por quanto tempo uma
entrega em `SENDING` fica reservada antes de poder ser retomada por outro
worker.

O worker compartilhado executa o lote e, ao concluir a coleta, chama a entrega
pendente. Também é possível reprocessar somente as notificações:

```powershell
py -3.14 manage.py deliver_pending_alerts --limit 100
```

Esse comando não cria eventos nem envia nada quando a lista está vazia. Use-o
apenas com um bot conectado quando quiser fazer uma validação real.

## Validação

Os testes usam clientes Telegram falsos e cobrem formatação HTML, botões,
idempotência, isolamento entre tenants, bot ausente, 401, 403, falha de
transporte, callback de pausa, cooldown do `/atualizar` e conclusão do job
manual. Não há token real nem tráfego para Telegram na suíte.

Antes de liberar o envio real, a validação humana necessária é: habilitar o bot
gerenciador, concluir a criação do bot pessoal, abrir o bot e enviar `/start`,
executar uma coleta controlada com uma queda simulada e confirmar uma única
mensagem no chat correto. O screenshot atual mostra o bot pessoal como
**Pendente**; portanto essa etapa humana ainda não foi executada neste
ambiente.
