# Operação e recuperação

## Rotina diária

O Compose mantém `web`, `worker`, `scheduler` e `monitor` com reinício automático.
O backup deve rodar diariamente em horário diferente da coleta:

```powershell
.\scripts\run_backup.ps1 -ValidateOnly
.\scripts\install_backup_task.ps1 -DailyAt 03:15 -RetentionDays 30 -Preview
.\scripts\install_backup_task.ps1 -DailyAt 03:15 -RetentionDays 30 -RunNow
```

Para sobreviver à perda do disco, informe um diretório externo já existente:

```powershell
.\scripts\install_backup_task.ps1 -ExportDirectory "D:\ChaRadarzin-backups" -RunNow
```

O backup usa `pg_dump` custom, manifesto e SHA-256. A limpeza de dados técnicos e
de backups só ocorre depois que um novo backup termina com sucesso.

## Verificações

```powershell
docker compose --env-file .env.compose ps
docker compose --env-file .env.compose exec -T web python manage.py operational_status --json
docker compose --env-file .env.compose exec -T web python manage.py purge_expired_data
docker compose --env-file .env.compose exec -T web python manage.py prune_backups
```

## Restauração controlada

Nunca restaure por cima do banco ativo sem uma janela de manutenção e um backup
novo. Primeiro valide o artefato, usando caminhos Linux dentro do contêiner:

```powershell
docker compose --env-file .env.compose exec -T web `
  python manage.py restore_database /app/backups/ARQUIVO.dump --verify-only
```

Para o ensaio seguro, prefira restaurar uma cópia em um projeto Compose isolado.
Uma restauração no ambiente principal exige autorização humana explícita, parada
de `web`, `worker`, `scheduler` e `monitor`, e então `--confirm`. Depois, suba os
serviços, confira `/health/ready/`, o status operacional e uma coleta manual.

## Incidentes

- Não compartilhe `.env.compose`, tokens, IDs de webhook, dumps ou logs brutos.
- Se um token vazar, revogue-o no BotFather e rotacione a chave/credencial.
- Se o PC ficar desligado, a coleta e o túnel ficam indisponíveis; ao religar,
  confirme Docker Desktop, Tailscale Funnel e os serviços Compose.
