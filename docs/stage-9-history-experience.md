# Etapa 9 — histórico e experiência

A página de produto agora responde, sem consultar logs, qual é a melhor oferta
válida e como ela se comporta no tempo. O resumo é calculado somente com
observações do próprio tenant e não mistura falhas de coleta, indisponibilidade
ou preços sem custo total.

## O que aparece no painel

- melhor oferta atual por custo total, com loja, vendedor, produto, frete,
  horário e link exato;
- preço atual, menor histórico, variação em relação ao dia anterior, preço-alvo,
  baseline e forma de pagamento;
- gráfico SVG acessível com até 30 dias e uma lista dos 10 dias mais recentes;
- eventos de alerta e estado da entrega (`Enviado`, `Falhou`, `Bloqueado` ou
  ainda não processado);
- estado do bot pessoal, além do estado legível da coleta;
- estados vazios para primeira coleta e ausência de preço comparável;
- aviso explícito quando a última tentativa falhou, preservando o histórico
  válido anterior.

O melhor preço de cada dia é usado no histórico. A melhor oferta atual é a menor
observação válida mais recente de cada fonte ativa; uma fonte pausada deixa de
participar da oferta atual, mas seus registros continuam no histórico já salvo.

## Isolamento e segurança

Todas as consultas de observações, eventos, fontes e bots filtram pelo tenant
autenticado. O link da oferta continua sendo validado no cadastro e protegido
com `target="_blank"` e `rel="noopener noreferrer"`. O gráfico não executa
JavaScript nem recebe conteúdo HTML de lojas.

## Validação

Os testes cobrem melhor oferta entre fontes, menor histórico, variação diária,
gráfico, evento enviado, bot conectado, estados vazios e dashboard isolado. A
validação visual pública foi feita após recarregar o app local sem erros de
console; a tela autenticada deve ser conferida pelo usuário com os dois SSDs já
cadastrados, confirmando os valores reais após a primeira coleta.
