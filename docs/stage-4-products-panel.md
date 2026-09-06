# Etapa 4 — produtos e painel

Esta etapa entrega o cadastro web de produtos por link exato, a confirmação
humana da identidade da oferta e o gerenciamento diário do radar. Ela não
executa a coleta compartilhada: a primeira observação e a baseline pertencem à
Etapa 5.

## Fluxo do usuário

1. A pessoa autenticada abre `/painel/` e escolhe **Adicionar produto**.
2. Cola uma URL HTTPS exata da Amazon Brasil ou da KaBuM.
3. O servidor normaliza a URL, aplica as proteções de rede e extrai uma prévia
   efêmera sem armazenar o HTML da loja.
4. A pessoa confirma nome, modelo, variante ou capacidade, vendedor, pagamento
   e, opcionalmente, preço-alvo.
5. Produto e fonte são criados em uma transação e ficam ativos no painel.
6. Outros links exatos podem ser adicionados ao mesmo produto pelo detalhe.

O painel também permite editar produto e validações da fonte, pausar produto ou
fonte, configurar CEP e pagamento e remover um produto do radar. Remover é uma
exclusão lógica: o produto fica arquivado, suas fontes são desativadas e o
histórico permanece preservado.

## Estado de coleta

Um produto recém-confirmado aparece como **Aguardando primeira coleta**. Depois
que observações existirem, o painel diferencia monitoramento válido,
indisponibilidade, falha que exige atenção, pausa, ausência de fontes e
arquivamento. O motor da Etapa 5 cria a baseline na primeira coleta válida sem
emitir alerta de variação.

## Proteção da prévia

- somente HTTPS, porta padrão e hosts exatos suportados;
- somente formatos reconhecidos de página de produto;
- credenciais na URL, fragmentos, IPs e domínios parecidos são rejeitados;
- resolução DNS precisa retornar apenas endereços públicos;
- redirecionamentos não são seguidos;
- timeout, limite de resposta e conteúdo HTML são verificados;
- tentativas são limitadas por tenant e persistidas no banco;
- a prévia expira e armazena apenas campos extraídos e códigos de falha seguros.

Os limites podem ser ajustados por `PRODUCT_PREVIEW_*` no ambiente.
Pré-visualizações expiradas também podem ser limpas explicitamente:

```powershell
python manage.py expire_product_previews
```

## Validação

Os testes automatizados usam fixtures locais e cobrem o fluxo de ativação no
site, idempotência, isolamento entre tenants, URLs maliciosas, bloqueio de DNS
privado, redirecionamentos, limite de resposta, rate limit, expiração, segunda
fonte, preferências, pausa e arquivamento. Eles não acessam lojas reais.

Antes de produção, uma pessoa deve validar ao menos um link final real de cada
loja habilitada e conferir visualmente título, modelo, variante, vendedor,
estoque e forma de pagamento. Anti-bot e alterações de HTML podem impedir uma
prévia mesmo quando a URL é legítima; nesse caso o sistema falha de forma
fechada e não ativa o produto.
