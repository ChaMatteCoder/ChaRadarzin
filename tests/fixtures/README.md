# Fixtures de teste

Todos os arquivos desta pasta são sintéticos e não contêm cookies, cabeçalhos
autenticados, CEPs, tokens ou dados pessoais. Eles representam apenas os campos
mínimos usados pelos parsers e regras do MVP.

- `amazon/` e `kabum/`: páginas de produto controladas.
- `shipping/`: respostas controladas de cotação e entrega.
- `prices/`: séries pequenas para baseline e variações de alerta.

Os testes automatizados devem usar estes arquivos e nunca depender de lojas ou
do Telegram reais.
