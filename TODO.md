# TODO — Phase 4 follow-up

Финальная сводка после завершения Phase 4 (CI/CD + production deploy machinery в репе).

## Phase 4 — финальная сводка

**Все 7 задач + 5 fix-коммитов от ревью + 1 финальный fix = 13 коммитов на `origin/main`** (от `ebc88b3` до `4207301`).

| Артефакт | Состояние |
|---|---|
| Спека | `docs/superpowers/specs/2026-05-02-phase-4-cicd-deploy-design.md` |
| План | `docs/superpowers/plans/2026-05-02-phase-4-cicd-deploy.md` |
| GH Actions workflow | `.github/workflows/build-image.yml` — пушит `:latest` + `:<sha>` в `ghcr.io` (private) на каждый push в main. **Прошёл успешно.** |
| Production compose | `docker-compose.prod.yml` (extractor image override + nginx service) |
| nginx + TLS | `nginx/templates/datalens.conf.template` (с HSTS, http2 on, default.conf masked) |
| Backup | `scripts/backup.sh` + `tests/test_backup.sh` (TDD-проверен, 33/33 интеграционных тестов всё ещё pass) |
| Provisioning | `scripts/provision-vm.sh` (idempotent, DEBIAN_FRONTEND, graceful clone fallback, certbot deploy hook) |
| Makefile | targets: `prod-up`/`prod-down`/`prod-logs`/`nginx-check` |
| README | "Production deploy" + "Operations runbook" (status, rollback, restore, TLS renewal, manual deploy, maintenance shutdown) |
| `.env.example` | новые переменные `GHCR_OWNER`, `SERVER_NAME` |

### Ревью находок (всё применено)

- **Task 1**: 2 Minor (defensive hygiene, не блокирующие).
- **Task 3**: 2 Important (deprecated `listen ... http2`, default.conf collision) + HSTS-headers.
- **Task 4**: 2 Important (shebang был на месте, но defaults для POSTGRES_USER/DB).
- **Task 5**: 2 Important (DEBIAN_FRONTEND, graceful clone fallback) + certbot deploy hook.
- **Task 6**: 2 Important (pg_restore→psql contradiction, stale TLS section).
- **Финальный обзор**: 1 Important (sudo в certbot hook убран).

## Что дальше — твоё

1. **Выбрать VM-провайдера.** Timeweb оказался дорогим; альтернативы — Selectel, RuVDS, Beget, Hetzner CX22 (если подходит для аудитории).
2. **Получить VM с характеристиками: 8 GB RAM, 50 GB диск, 2 vCPU, Ubuntu 24.04 LTS.**
3. **Купить домен ИЛИ использовать поддомен провайдера** — при условии что можно прописать A-запись на VM-IP. Иначе certbot HTTP-challenge не сработает.
4. **На VM:**
   ```bash
   sudo bash scripts/provision-vm.sh
   ```
   Затем пройти 6 шагов manual handoff из вывода скрипта:
   - `scp .env`/`.cert` с ноута на VM
   - Отредактировать `.env` (`GHCR_OWNER`, `SERVER_NAME` + остальные секреты)
   - `docker login ghcr.io` с GitHub PAT (`read:packages`)
   - `certbot certonly --standalone -d <домен>`
   - `cd /srv/extractor && make prod-up`
   - Сменить `admin/admin` через DataLens UI

После этого `https://поддомен/` отдаст DataLens с TLS, и команда увидит дашборды.
