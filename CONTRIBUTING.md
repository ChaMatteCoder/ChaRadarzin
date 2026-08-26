# Contribuindo com o ChaRadarzin

Obrigado pelo interesse no projeto.

## Fluxo recomendado

1. Crie uma branch curta a partir da branch principal.
2. Faça mudanças pequenas e focadas.
3. Nunca adicione `.env`, bancos, logs, relatórios ou credenciais.
4. Execute os testes antes de abrir um pull request:

```powershell
py -m unittest discover -s tests -v
```

5. Descreva no pull request o comportamento alterado, riscos e validações.

## Novas lojas

Cada integração deve ter um coletor isolado, validar domínio, modelo, variante,
vendedor, estoque e forma de pagamento, além de testes com respostas estáticas.
Falhas de parsing ou frete não podem ser transformadas em preços válidos.

## Segurança e privacidade

Não inclua respostas autenticadas, cookies ou dados pessoais em fixtures.
Use exemplos sintéticos e consulte a [política de segurança](SECURITY.md) para
relatar vulnerabilidades.
