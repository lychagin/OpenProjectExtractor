"""Create subtasks under feature WP #6725 in OpenProject.

Source plan:
  ~/source/predubezhdai/docs/plan/products-sort-in-admin-decomposition.md

Usage:
  # Dry-run: show what would be created without hitting the API
  python scripts/create_product_sort_subtasks.py --dry-run

  # Create everything
  python scripts/create_product_sort_subtasks.py

  # Create only specific tasks (by ID prefix from decomposition)
  python scripts/create_product_sort_subtasks.py --only B1,B2,F1

  # Resume after failure: skip tasks already created (by stored mapping in output/)
  python scripts/create_product_sort_subtasks.py --resume
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import requests
from dotenv import load_dotenv


load_dotenv()

OPENPROJECT_URL = os.getenv('OPENPROJECT_URL', 'https://projects-customdev.wone-it.ru')
TOKEN = os.getenv('OPENPROJECTTOKEN')
CERT_PATH = Path(__file__).resolve().parent.parent / '.cert' / 'bundle.pem'

PROJECT_ID = 24                # ТП "Предубеждай"
PARENT_WP_ID = 6725            # Feature: Создание раздела для сортировки продуктов в админ-панели
TASK_TYPE_ID = 1               # Type "Task"
DEFAULT_PRIORITY_ID = 8        # Normal

OUTPUT_DIR = Path(__file__).resolve().parent.parent / 'output'
MAPPING_FILE = OUTPUT_DIR / 'product_sort_subtasks_created.json'


@dataclass
class Task:
    code: str          # B1, F3, C2 etc — identifier from decomposition
    subject: str       # Task title
    hours: float       # estimated time in hours
    body: str          # markdown description


def hours_to_iso8601(hours: float) -> str:
    """Convert decimal hours to ISO 8601 duration (PT1H30M)."""
    total_minutes = int(round(hours * 60))
    h, m = divmod(total_minutes, 60)
    parts = ['PT']
    if h:
        parts.append(f'{h}H')
    if m or not h:
        parts.append(f'{m}M')
    return ''.join(parts) if (h or m) else 'PT0M'


# ---------------------------------------------------------------------------
# Task list — mirrors docs/plan/products-sort-in-admin-decomposition.md
# Each body uses GitHub-flavored markdown; OpenProject renders it.
# ---------------------------------------------------------------------------

TASKS: list[Task] = [
    # --- Backend ---
    Task('B1', 'B1. Backend: модуль `product-sort` — скелет, DTO, регистрация в admin', 1.0, """\
**Что сделать:**
- Создать `server/src/admin/product-sort/` со структурой: `product-sort.module.ts`, `product-sort.controller.ts`, `product-sort.service.ts`.
- Подключить модуль в `server/src/admin/admin.module.ts`.
- Прокинуть `SessionGuard({ role: 'admin' })` на контроллер.
- Подключить репозитории `Product`, `SKU`, `Stock`, `Storage`, `Category`, `ProductCategories` через `MikroOrmModule.forFeature`.
- Создать пустые DTO-стабы под §8.1, §8.3 (детальная реализация — в C1).

**Definition of Done:**
- Модуль импортируется в `admin.module.ts`, проект компилируется и стартует.
- Заглушечный `GET /api/admin/product-sort` отвечает `200` пустым массивом под админской сессией.
"""),
    Task('B2', 'B2. Backend: CSV-утилита (парсер + сериализатор, BOM, RFC 4180)', 2.0, """\
**Что сделать:**
- Утилита `server/src/admin/product-sort/csv.util.ts`.
- Сериализатор: разделитель `;`, CRLF, UTF-8 BOM в начале, RFC 4180 экранирование (двойные кавычки + удвоение внутренних).
- Парсер: толерантно принимает UTF-8 c/без BOM; CR/CRLF/LF; экранированные кавычки. Возвращает массив объектов с заголовками.
- Unit-тесты на edge cases: `;` внутри значения, кавычки внутри значения, перевод строки в значении, пустая строка, BOM, пустые ячейки.

**Definition of Done:**
- Тесты `csv.util.spec.ts` зелёные.
- Утилита переиспользуется в B6 и B7.
"""),
    Task('B3', 'B3. Backend: агрегат «Сток, шт» + unit-тесты', 2.0, """\
**Что сделать:**
- Метод `ProductSortService.computeStockTotalsForProductIds(productIds: string[]): Map<string, { stockTotal: number; hasStock: boolean }>`.
- Реализация через MikroORM QueryBuilder или `EntityManager.execute` по SQL из §8.1 плана v2 (`LEFT JOIN sku → stock → storage WHERE storage.active = true`, `GROUP BY p.id`).
- Unit-тесты с фикстурами:
  1. Товар без SKU → `stockTotal=0, hasStock=false`.
  2. Товар с SKU без записей в `stock` → `stockTotal=0, hasStock=false`.
  3. Товар с SKU и стоком на одном складе → корректная сумма.
  4. Товар с несколькими SKU и складами → сумма по всем.
  5. Запись `stock` на неактивном складе (`storage.active=false`) — **исключается** из агрегата.

**Definition of Done:**
- Сервис возвращает `Map` с корректными значениями для всех 5 кейсов.
- Тесты зелёные.
"""),
    Task('B4', 'B4. Backend: `GET /api/admin/product-sort` (фильтры, сортировка, пагинация)', 3.0, """\
**Что сделать:**
- Реализация контроллера и сервиса по §8.1.
- Query-параметры: `title`, `article`, `sortBy ∈ {sort_order|sort_section|stock_total}`, `sortDir`, `page`, `pageSize ∈ {25|50|100}`.
- Валидация query через `class-validator` (DTO).
- Сборка ответа: `{ items, total, page, pageSize }`.
- Поля каждого item: `productId`, `title`, `article`, `stockTotal`, `hasStock`, `sortOrder`, `sortSection`.
- `total` через отдельный COUNT с теми же фильтрами.
- Сортировка `stock_total` — на уровне SQL через GROUP BY + ORDER BY (использовать B3).

**Definition of Done:**
- Эндпоинт отвечает корректно для 4 кейсов: пустые фильтры, фильтр по title, фильтр по article, сортировка по каждому полю.
- Логи pino не содержат WARN/ERROR.
"""),
    Task('B5', 'B5. Backend: `PATCH /api/admin/product-sort` (bulk save сортировок)', 1.5, """\
**Что сделать:**
- Реализация по §8.3: bulk-апдейт `sort_order` / `sort_section`.
- DTO с валидацией: `productId: string`, `sortOrder?: int >= 0`, `sortSection?: int >= 0`.
- Транзакция через `EntityManager.transactional()`.
- Возврат: актуальные строки в формате §8.1 (для перерисовки таблицы).
- Pino-лог `info` с adminLogin, числом затронутых строк.
- При несуществующем `productId` → `400` с перечнем ошибочных id.

**Definition of Done:**
- Атомарный апдейт работает; при ошибке в одной строке откатываются все.
- Несуществующий `productId` → `400` с понятным сообщением.
"""),
    Task('B6', 'B6. Backend: `GET /export` + `POST /import` для основной страницы', 2.5, """\
**Что сделать:**
- `GET /api/admin/product-sort/export`: переиспользует фильтрацию из B4, выгружает **все страницы**, отдаёт `text/csv` + `Content-Disposition: attachment`. Колонки: `product_id;article;title;stock_total;sort_order;sort_section`.
- `POST /api/admin/product-sort/import` (`multipart/form-data`, `@nestjs/platform-express` `FileInterceptor`): парсит CSV через B2, валидирует обязательные колонки (`product_id` + хотя бы одно из `sort_order|sort_section`), игнорирует `stock_total`/`article`/`title` на запись.
- Частичный импорт: пропуск отсутствующих `product_id`, агрегация ошибок в ответе по схеме §8.4.
- Pino-лог `info` с именем файла, числом updated/skipped.

**Definition of Done:**
- Экспорт открывается в Excel без кракозябр (BOM работает).
- Импорт CSV из 100 строк (5 битых) → 95 updated, 5 errors в ответе.
- Полностью невалидный файл → `400`.
"""),
    Task('B7', 'B7. Backend: категорийные эндпоинты (`GET`, `PATCH`, `export`, `import`)', 3.0, """\
**Что сделать (по §8.5):**
- `GET /api/admin/categories` — если уже есть в админке, переиспользовать; иначе быстрый эндпоинт со списком `{ id, title }[]`.
- `GET /api/admin/product-sort/categories/:categoryId` — постраничный список товаров категории; те же query-параметры, что в B4, но `sortBy ∈ {sort_category|stock_total}`. Item: `{ productId, title, article, stockTotal, hasStock, sortCategory }`.
- `GET /api/admin/product-sort/categories/:categoryId/export` — CSV; колонки: `product_id;category_id;article;title;stock_total;sort_category`.
- `PATCH /api/admin/product-sort/categories` — bulk-апдейт `sort_category` по парам `(productId, categoryId)` в `product_categories`. Транзакция, last-write-wins. Несуществующая пара → `400`.
- `POST /api/admin/product-sort/categories/import` — импорт CSV, matching по `(product_id, category_id)`, остальное аналогично B6.

**Definition of Done:**
- Все 5 эндпоинтов работают; smoke-тест на категории с 50 товарами проходит.
- Импорт обновляет несколько категорий за один файл (благодаря тому, что `category_id` берётся из строки).
"""),
    Task('B8', 'B8. Backend: отключение синхронизации `sort_*` в Exchange Service', 1.0, """\
**Что сделать:**
- В коде exchange-сервиса (`server/src/exchange/`) найти места маппинга DTO от 1С на `Product` и `ProductCategories`.
- Удалить запись в поля `product.sort_order`, `product.sort_section`, `product_categories.sort_category` (если есть).
- Если поля приходят из 1С — игнорировать на этапе маппинга.
- Оставить комментарий со ссылкой на ТЗ v2.
- Проверить, что `stock.quantity` / `stock.reserved_quantity` синхронизация продолжает обновлять.

**Definition of Done:**
- Code review подтверждает, что `sort_*` поля не пишутся.
- Smoke на тестовом стенде: ручное изменение сортировки → запуск синхронизации → значение не затёрлось.
"""),
    Task('B9', 'B9. Backend: инвалидация кэша каталога/коллекций/категорий', 0.5, """\
**Что сделать:**
- Найти, есть ли in-memory кэш сортировок в коде каталога (`server/src/product/`, `server/src/section/`, `server/src/category/`).
- Если есть — после `PATCH` / `import` (B5, B6, B7) дёргать сброс этого кэша.
- Если кэша нет — задокументировать в коде (комментарий) и закрыть задачу.

**Definition of Done:**
- После сохранения сортировки запрос `POST /api/pages/catalog` с `sort: "DEFAULT"` отдаёт новый порядок без рестарта сервера.
"""),
    Task('B10', 'B10. Backend: e2e-тесты для всех эндпоинтов', 3.0, """\
**Что сделать:**
- Тесты в `server/test/` (или там, где лежат остальные e2e):
  - `GET /api/admin/product-sort` с разными фильтрами и сортировками.
  - `PATCH /api/admin/product-sort` happy path + ошибки валидации.
  - `GET /export` — проверка CSV-структуры (заголовок, BOM, разделитель).
  - `POST /import` — happy + частично битый файл.
  - Полный набор для категорийных эндпоинтов (B7).

**Definition of Done:**
- `npm run test:e2e` зелёный, новые тесты входят в pipeline.
"""),
    # --- Frontend ---
    Task('F1', 'F1. Frontend: подменю «Сортировка» + роутинг + 2 страницы-болванки', 1.0, """\
**Что сделать:**
- В навигации админки добавить подменю «Сортировка» с двумя пунктами: «Сортировка товаров» и «Сортировка в категории».
- Роуты: `/sort/products` и `/sort/categories`.
- Болванки страниц с заголовком и пустым контейнером в стиле остальной админки.

**Definition of Done:**
- Пункты меню видны под админской сессией, переход на оба роута открывает соответствующие пустые страницы.
"""),
    Task('F2', 'F2. Frontend: Effector store + API-клиент для основной страницы', 1.5, """\
**Что сделать:**
- Store `admin/store/product-sort.ts` (по аналогии с существующими сторами админки).
- Effects: `fetchProductSortFx`, `bulkUpdateProductSortFx`, `exportProductSortFx`, `importProductSortFx`.
- Stores: `$items`, `$total`, `$page`, `$pageSize`, `$filters`, `$sort`, `$pendingChanges`, `$isLoading`.
- Axios-клиент в `admin/api/product-sort.ts`.

**Definition of Done:**
- Store покрывает все нужды страницы (F3–F7), типы импортируются из `@dto/src/admin/product-sort/*`.
"""),
    Task('F3', 'F3. Frontend: страница «Сортировка товаров» — layout + фильтры + URL-state', 2.0, """\
**Что сделать:**
- Структура страницы: панель фильтров (`title`, `article`), кнопка «Применить», слот для кнопок Экспорт/Импорт, контейнер таблицы, пагинация снизу.
- Сохранение фильтров, сортировки, страницы в URL query-параметрах.
- Чтение URL при монтировании → инициализация store.
- «Применить» дёргает `fetchProductSortFx` с актуальными фильтрами.

**Definition of Done:**
- Перезагрузка страницы с заполненным URL восстанавливает фильтры и состояние.
- Поделиться ссылкой → коллега видит то же самое.
"""),
    Task('F4', 'F4. Frontend: таблица — 5 колонок, серверная сортировка, пагинация', 2.5, """\
**Что сделать:**
- MUI DataGrid (используется в админке) или таблица в существующем стиле проекта.
- Колонки: «Название», «Артикул», «Сток, шт» (выравнивание справа, «—» если `!hasStock`, серый цвет если `stockTotal === 0`), «Порядок в каталоге», «Порядок в коллекции».
- Заголовки колонок 3, 4, 5 — кликабельные (▲▼), single-column sort, 3 состояния (asc / desc / reset).
- Пагинация снизу: dropdown 25/50/100 (по умолчанию 100), стрелки ←/→, текст «Страница X из Y».
- Сброс страницы на 1 при смене pageSize/фильтров.

**Definition of Done:**
- Все 5 колонок отображаются корректно для всех типов товаров (с/без стока).
- Сортировка по 3 колонкам работает с серверной стороны.
"""),
    Task('F5', 'F5. Frontend: inline-редактирование ячеек + валидация + подсветка', 2.0, """\
**Что сделать:**
- В колонках 4 и 5 клик по ячейке → input `type=number`, `min=0`, `step=1`.
- Валидация: целое `>= 0`. Невалидно → красная рамка, ошибка в `$pendingChanges`.
- Изменённая ячейка визуально подсвечивается до сохранения (фон).
- Blur / Tab / Enter → сворачивание input в текст; значение хранится в `$pendingChanges`.
- Колонка «Сток, шт» **не редактируется** — клик по ней игнорируется.

**Definition of Done:**
- Можно изменить 10 ячеек подряд, все подсвечены, в стейте лежат ровно 10 изменений.
- Невалидный ввод не попадает в стейт сохранения.
"""),
    Task('F6', 'F6. Frontend: кнопка «Изменить порядок» + bulk-save + обновление таблицы', 1.5, """\
**Что сделать:**
- Кнопка под таблицей. `disabled`, если `$pendingChanges` пустой или содержит невалидные строки.
- При клике → `bulkUpdateProductSortFx` с массивом изменений.
- При успехе: обновить таблицу актуальными данными из ответа (включая `stockTotal`), снять подсветку, показать snackbar/toast.
- При ошибке валидации с бэка → показать сообщение.

**Definition of Done:**
- Сохранение 10 изменений → таблица перерисована, подсветка снята, тост «Сохранено».
"""),
    Task('F7', 'F7. Frontend: «Экспорт» / «Импорт» UI на основной странице', 2.0, """\
**Что сделать:**
- Кнопка «Экспорт» с tooltip «Экспорт в Excel» → дёргает `GET /export` с текущими фильтрами, скачивает файл (через blob + `<a download>`).
- Кнопка «Импорт» с tooltip «Импорт данных из Excel» → системный диалог `<input type="file" accept=".csv">` → POST файла.
- Обработка ответа: если есть `errors[]` → модалка с топ-5 + «и ещё N».
- При success → перезагрузить таблицу.

**Definition of Done:**
- Экспорт открывается в Excel под Windows без кракозябр.
- Импорт битого файла показывает корректную модалку с ошибками.
"""),
    Task('F8', 'F8. Frontend: Effector store + API-клиент для категорийной страницы', 1.0, """\
**Что сделать:**
- Аналог F2: `admin/store/product-sort-categories.ts`, effects `fetchCategoriesListFx`, `fetchProductsByCategoryFx`, `bulkUpdateSortCategoryFx`, `exportFx`, `importFx`.

**Definition of Done:**
- Store покрывает нужды F9–F10.
"""),
    Task('F9', 'F9. Frontend: страница «Сортировка в категории» — dropdown + таблица', 2.0, """\
**Что сделать:**
- Сверху — dropdown категорий с поиском по названию (Autocomplete MUI).
- Под dropdown — те же фильтры (`title`, `article`) и кнопка «Применить».
- Таблица с 4 колонками: Название, Артикул, **Сток, шт**, Порядок в категории.
- Inline-редактирование `sort_category`, сортировка (по `sort_category` / `stock_total`), пагинация — переиспользовать компоненты из F4–F6 (вынести в общий компонент при необходимости).
- Кнопка «Изменить порядок» → `bulkUpdateSortCategoryFx`.

**Definition of Done:**
- Выбор категории → загрузка её товаров; все механики работают идентично основной странице.
"""),
    Task('F10', 'F10. Frontend: «Экспорт» / «Импорт» UI на категорийной странице', 1.0, """\
**Что сделать:**
- Кнопки рядом с фильтрами; экспорт дёргает `/categories/:id/export` с текущими фильтрами, импорт постит в `/categories/import`.
- Модалка ошибок переиспользуется из F7.

**Definition of Done:**
- Экспорт/импорт работают для выбранной категории; CSV содержит `category_id` в каждой строке.
"""),
    # --- Cross-cutting ---
    Task('C1', 'C1. DTO: типы и валидаторы в `/dto/src/admin/product-sort/`', 1.0, """\
**Что сделать:**
- DTO для запроса/ответа всех эндпоинтов §8 плана v2, с `class-validator` декораторами.
- Сборка `dto/dist/` (`npm run build` в пакете `dto`).
- Импорты с обеих сторон через `@dto/src/admin/product-sort/*`.

**Definition of Done:**
- Server и admin компилируются с использованием новых DTO.
"""),
    Task('C2', 'C2. Manual QA / regression на тестовом стенде', 2.0, """\
**Что сделать:**
- Прогон всех сценариев DoD из §11 плана v2.
- Проверка инвалидации витрины: изменение сортировки → каталог обновлён.
- Проверка, что Exchange Service не затирает значения (запуск синхронизации после правки).
- Проверка экспорта в Excel под Windows.
- Регресс смежного функционала: каталог, карточка товара, существующая админка товаров.

**Definition of Done:**
- Все пункты DoD из §11 v2 отмечены.
- Регрессов в смежном функционале не выявлено.
"""),
    Task('C3', 'C3. Code review iteration + правки', 1.0, """\
**Что сделать:**
- Реакция на ревью PR (backend + frontend), доработки, повторная сборка/тесты.
- Финальный squash/cleanup коммитов перед мерджем.

**Definition of Done:**
- PR approved, CI зелёный, смерджен в `main`.
"""),
]


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def auth():
    if not TOKEN:
        sys.exit('OPENPROJECTTOKEN is required (set in .env)')
    return ('apikey', TOKEN)


def headers():
    return {'Accept': 'application/json', 'Content-Type': 'application/json'}


def build_payload(task: Task) -> dict:
    return {
        'subject': task.subject,
        'description': {'format': 'markdown', 'raw': task.body},
        'estimatedTime': hours_to_iso8601(task.hours),
        '_links': {
            'type': {'href': f'/api/v3/types/{TASK_TYPE_ID}'},
            'parent': {'href': f'/api/v3/work_packages/{PARENT_WP_ID}'},
            'priority': {'href': f'/api/v3/priorities/{DEFAULT_PRIORITY_ID}'},
        },
    }


def create_work_package(task: Task) -> dict:
    url = f'{OPENPROJECT_URL}/api/v3/projects/{PROJECT_ID}/work_packages'
    r = requests.post(
        url,
        auth=auth(),
        headers=headers(),
        json=build_payload(task),
        verify=str(CERT_PATH),
        timeout=30,
    )
    if r.status_code >= 400:
        sys.stderr.write(f'[{task.code}] HTTP {r.status_code}: {r.text}\n')
        r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Mapping persistence
# ---------------------------------------------------------------------------

def load_mapping() -> dict[str, int]:
    if MAPPING_FILE.exists():
        return json.loads(MAPPING_FILE.read_text(encoding='utf-8'))
    return {}


def save_mapping(mapping: dict[str, int]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    MAPPING_FILE.write_text(json.dumps(mapping, indent=2, ensure_ascii=False), encoding='utf-8')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dry-run', action='store_true',
                   help='Do not call the API; print payloads instead.')
    p.add_argument('--only', type=str, default='',
                   help='Comma-separated list of task codes to create (e.g. B1,B2,F1).')
    p.add_argument('--resume', action='store_true',
                   help=f'Skip tasks already created (per {MAPPING_FILE}).')
    return p.parse_args()


def filter_tasks(args: argparse.Namespace, mapping: dict[str, int]) -> list[Task]:
    selected = TASKS
    if args.only:
        wanted = {c.strip() for c in args.only.split(',') if c.strip()}
        selected = [t for t in selected if t.code in wanted]
    if args.resume:
        selected = [t for t in selected if t.code not in mapping]
    return selected


def main() -> int:
    args = parse_args()
    mapping = load_mapping()
    tasks = filter_tasks(args, mapping)

    if not tasks:
        print('Nothing to do.')
        return 0

    total_hours = sum(t.hours for t in tasks)
    print(f'About to create {len(tasks)} task(s) under WP #{PARENT_WP_ID} '
          f'in project {PROJECT_ID} (total {total_hours} h).')
    if args.dry_run:
        print('(dry-run — no API calls)\n')
        for t in tasks:
            print(f'[{t.code}] {t.subject}  ({hours_to_iso8601(t.hours)})')
        return 0

    for t in tasks:
        wp = create_work_package(t)
        wp_id = wp.get('id')
        mapping[t.code] = wp_id
        save_mapping(mapping)
        print(f'[{t.code}] created WP #{wp_id}: {wp.get("subject")}')

    print(f'\nDone. Mapping saved to {MAPPING_FILE}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
