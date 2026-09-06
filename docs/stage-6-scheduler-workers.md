# Etapa 6 — agendamento e workers

A coleta web agora usa uma fila persistida no banco Django. O scheduler apenas
cria o job diário; o worker o reserva e executa o motor compartilhado da Etapa
5. Essa separação permite executar o painel e os workers em processos distintos
sem exigir Redis no desenvolvimento local.

## Arquitetura

`CollectionJob` representa a unidade idempotente de trabalho. O job diário usa
a chave `daily-collection:AAAA-MM-DD`, portanto várias chamadas do scheduler no
mesmo dia não duplicam a coleta. `CollectionJobAttempt` registra cada tentativa,
worker, duração, lote produzido e código seguro de falha.

Estados da fila:

```text
QUEUED -> RUNNING -> SUCCESS
             |
             +-> RETRY_WAIT -> RUNNING
             |
             +-> DEAD
```

`CollectionWorkerLease` fornece exclusão mútua para o pipeline compartilhado.
Um segundo worker não inicia outra coleta enquanto a reserva estiver válida. Se
um processo desaparecer, a reserva expira, a tentativa fica `ABANDONED` e o job
volta com backoff. O prazo padrão é propositalmente maior que uma coleta normal;
ajuste-o apenas com base em durações observadas.

Erros de infraestrutura usam backoff exponencial limitado. Erros de
configuração e falhas de programação classificadas como não recuperáveis vão
diretamente para `DEAD`. Falhas de uma fonte que o motor consegue isolar
continuam produzindo um lote `PARTIAL` e não repetem todas as consultas.

## Comandos

Aplicar a migração:

```powershell
py -3.14 manage.py migrate
```

Executar o scheduler idempotente:

```powershell
py -3.14 manage.py schedule_daily_collection
```

Criar um job imediato apenas para validação operacional:

```powershell
py -3.14 manage.py schedule_daily_collection --force
```

Processar um único job, adequado ao Agendador do Windows:

```powershell
py -3.14 manage.py run_collection_worker --once
```

Em um ambiente de servidor, mantenha um worker separado em execução:

```powershell
py -3.14 manage.py run_collection_worker
```

Depois de investigar e corrigir a causa de um job `DEAD`, reenfileire somente o
UUID revisado:

```powershell
py -3.14 manage.py retry_dead_collection_job UUID_DO_JOB
```

O histórico anterior é preservado e um novo conjunto de tentativas é liberado.

## Windows

O executor `scripts/run_scheduled.ps1` chama primeiro o scheduler e depois um
worker `--once`. Ele não usa mais o pipeline legado da planilha. O horário
informado ao instalador é repassado ao scheduler, enquanto a chave diária evita
duplicação entre o gatilho de logon e o gatilho diário.

Valide sem registrar uma tarefa:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts\run_scheduled.ps1 -DailyAt 21:05 -ValidateOnly

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts\install_scheduled_task.ps1 -DailyAt 21:05 -Preview
```

Registrar ou atualizar a tarefa continua sendo uma ação humana explícita:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts\install_scheduled_task.ps1 -DailyAt 21:05
```

## Configuração

- `COLLECTION_DAILY_TIME`: horário local usado fora do script Windows;
- `COLLECTION_JOB_MAX_ATTEMPTS`: tentativas iniciais de cada job;
- `COLLECTION_RETRY_BASE_SECONDS`: primeiro intervalo de backoff;
- `COLLECTION_RETRY_MAX_SECONDS`: teto do backoff exponencial;
- `COLLECTION_JOB_LEASE_SECONDS`: validade da reserva exclusiva;
- `COLLECTION_WORKER_POLL_SECONDS`: espera do worker contínuo sem trabalho.

## Limites conhecidos

A fila em SQLite serve ao desenvolvimento local, mas não é a configuração de
produção. Para deploy com mais de um processo use PostgreSQL, conforme a Etapa
2. A reserva tem prazo e não é um cancelamento forçado: coletores continuam
dependendo dos timeouts defensivos de página e frete. Uma indisponibilidade mais
longa que todas as tentativas exige diagnóstico e reprocessamento explícito.

Os testes da fila usam coletores simulados e não acessam lojas, Telegram ou CEP.
A certificação operacional continua exigindo uma coleta controlada com links
finais e um CEP autorizado pelo usuário.
