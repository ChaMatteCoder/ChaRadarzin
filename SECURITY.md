# Política de segurança

## Versões suportadas

Este repositório contém um protótipo em evolução. Correções de segurança são
aplicadas somente à versão mais recente da branch principal.

## Como reportar uma vulnerabilidade

Não publique tokens, credenciais, CEPs, identificadores de chat, cookies ou
outros dados privados em issues, discussões ou pull requests.

Use o recurso **Security Advisories** do GitHub no repositório para relatar uma
vulnerabilidade de forma privada. Inclua uma descrição objetiva, impacto,
passos mínimos para reprodução e, quando possível, uma sugestão de correção.

## Dados que nunca devem ser versionados

- arquivos `.env` reais;
- tokens de bots e identificadores privados de chat;
- CEPs ou outros dados de localização;
- bancos SQLite, arquivos de lock, logs e relatórios de execução;
- cookies, cabeçalhos de sessão ou respostas brutas autenticadas;
- planilhas ou artefatos temporários contendo dados pessoais.

O arquivo `.env.example` deve conter somente nomes de variáveis e valores
fictícios.
