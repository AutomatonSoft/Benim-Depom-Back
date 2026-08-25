# BENIM DEPOM — Backend

Backend сервиса, где продавцы создают товары из мобильного приложения, а менеджеры проверяют, дополняют и публикуют их на OTTO, Hood и Kaufland от аккаунтов JV и XL.

Проект — модульный Django-монолит. PostgreSQL хранит бизнес-данные и историю, Redis/Celery выполняют фоновые операции, FTP/FTPS хранит изображения. Архитектура рассчитана на сотни пользователей с запасом примерно до 1 000 активных пользователей.

## Возможности

- Регистрация seller по username, email и паролю; подтверждение email шестизначным SMTP-кодом.
- JWT: login, refresh с rotation, logout с blacklist refresh-token, профиль.
- Роли `seller`, `manager`, `admin` и разграничение доступа.
- Товары с ценой за единицу (`TRY`, `EUR`, `USD`), вариантами, размерами, количеством, HEX-цветом и текстовыми материалами.
- Выбор OTTO-категории и необязательных атрибутов из локального JSON-каталога.
- FTP/FTPS-загрузка фото, сортировка, удаление и главное изображение.
- Генерация белого фона, интерьерного и human-изображения внешним AI-сервисом.
- Модерация, история решений, сообщения продавцу, ручные и автоматические запросы наличия.
- EAN pool JV/XL; два EAN назначаются только при одобрении товара.
- In-app уведомления в БД и Firebase Cloud Messaging push.
- AI-черновики title, description и bullet points через OpenAI для marketplace-конфигураций.
- Подготовка, публикация, поиск, обновление, удаление/деактивация и отслеживание статусов OTTO, Hood и Kaufland.
- Swagger, Scalar, Django Admin, Docker Compose, pytest unit/integration/e2e тесты.

Не реализовано намеренно: чат менеджер ↔ продавец, SMS/phone authentication и Firebase Authentication. Firebase используется только для FCM push, телефон пока хранится как строка без проверки.

## Роли

| Роль | Как создаётся | Возможности |
| --- | --- | --- |
| `seller` | Публичная регистрация | Только свои товары, фото, профиль, submit, withdraw, ответ о наличии, заявка на деактивацию, уведомления. |
| `manager` | Защищённый endpoint web-панели | Все товары, модерация, EAN, уведомления, AI-фото, AI-контент, marketplace-конфигурации и операции. |
| `admin` | Только разработчик через `createsuperuser` / Django Admin | Всё, что manager, плюс Django Admin. |

Публичный `POST /api/v1/auth/register/` всегда создаёт только `seller`: передать роль в payload нельзя. Manager/admin не создаются публично. API-права определяет `role`; `is_staff` нужен лишь для Django Admin.

## Жизненный цикл товара

```text
Seller создаёт draft
  → добавляет variants и source images
  → выбирает OTTO category + optional attributes
  → submit на модерацию
  → manager проверяет/редактирует, при необходимости запускает AI-фото
  → approve: назначаются EAN JV и EAN XL
  → manager готовит marketplace configurations и AI-content draft
  → publish через orchestrator
  → Celery ожидает внешнее подтверждение и сохраняет статус публикации.
```

| Статус товара | Значение |
| --- | --- |
| `draft` | Черновик. Seller редактирует товар, варианты и фото. |
| `submitted` | Отправлен manager-у на модерацию. |
| `under_review` | Резерв под явный этап проверки. |
| `approved` | Одобрен, EAN назначены, можно публиковать. |
| `rejected` | Отклонён; seller исправляет и отправляет повторно. |
| `deactivated` | Отключён в бизнес-процессе после снятия публикаций. |
| `archived` | Мягко удалён: скрыт из рабочих списков, история сохранена. |

Публикация имеет отдельный статус: `pending`, `publishing`, `active`, `deactivating`, `deactivated`, `deleting`, `deleted`, `failed`. UI должен показывать статус товара и публикаций раздельно.

## Архитектура

```text
Flutter mobile / Web panel
          │ HTTPS + JWT
          ▼
      Django REST API
   ┌──────┼─────────┐
   ▼      ▼         ▼
PostgreSQL Redis   FTP/FTPS
           │         └─ source/generated images
           ▼
        Celery workers
   ┌──────┼─────┬─────┬──────────────┐
 marketplace AI images notifications maintenance
   ▼
OTTO / Hood / Kaufland / OpenAI / image AI / Firebase
```

| Queue | Worker | Concurrency | Задачи |
| --- | --- | ---: | --- |
| `marketplace` | `worker_marketplace` | 2 | Publish/update/search/delete, OTTO polling. |
| `ai` | `worker_ai` | 2 | OpenAI marketplace content. |
| `images` | `worker_images` | 2 | AI-фото и ожидание результатов. |
| `notifications` | `worker_notifications` | 2 | FCM push, availability reminders. |
| `maintenance` | `worker_maintenance` | 1 | Очистка и восстановление зависших задач. |

Одновременно может выполняться до 9 задач. Redis — broker/cache, но не источник бизнес-данных: товары, jobs, публикации и уведомления находятся в PostgreSQL.

### OTTO polling

После OTTO publish/update/activate/deactivate есть два этапа:

1. Проверка wrapper-процесса — время `pingAfter` от OTTO либо fallback каждые 30 секунд, максимум 120 попыток (~1 час).
2. Проверка реального marketplace status — каждые 5 минут, максимум 288 попыток (до 24 часов). Для publish/update/activate ожидается `ONLINE`, для deactivate — `INACTIVE`.

Поэтому финальный успех показывается только когда обновились и `MarketplaceJob`, и `MarketplacePublication`.

## Контракт для Flutter-разработчика

### Общие правила

- Base URL локально: `http://localhost:8000/api/v1`.
- JSON для всех запросов, кроме фото (`multipart/form-data`).
- В protected запросах: `Authorization: Bearer <access>`.
- Access живёт 15 минут, refresh — 7 дней. Хранить их только в `flutter_secure_storage`.
- При `401`: один раз вызвать `/auth/refresh/`, сохранить новую пару токенов и повторить запрос. Если refresh не прошёл — logout.
- Refresh ротируется: старый refresh всегда заменяется значением из ответа.
- Списки пагинированы: `count`, `page`, `limit`, `next`, `previous`, `results`.
- Если Swagger у endpoint показывает заголовок `Idempotency-Key`, создавать UUID на одно действие пользователя и использовать тот же UUID только для повторной отправки из-за сетевой ошибки.

### Регистрация и login

1. `POST /auth/register/` создаёт seller и отправляет email-code.
2. Показать экран ввода кода.
3. `POST /auth/email/verify/` возвращает `access` и `refresh`.
4. Если код не пришёл: `POST /auth/email/resend-verification/`.
5. Для следующих входов: `POST /auth/login/`.

```json
{
  "username": "seller_01",
  "email": "seller@example.com",
  "password": "StrongPassword123!",
  "password_confirm": "StrongPassword123!",
  "phone": "+905550000000",
  "preferred_language": "tr"
}
```

### FCM и уведомления

После login и после смены FCM-token зарегистрировать устройство:

```http
POST /api/v1/notifications/devices/
```

```json
{
  "token": "firebase-fcm-device-token",
  "platform": "android"
}
```

`platform`: `android` или `ios`. При logout/удалении устройства вызвать `POST /notifications/devices/deactivate/` с тем же token.

Push — только сигнал. После push и при открытии приложения загружать `GET /notifications/`, затем подтверждать прочтение.

### Форма товара

1. Запросить OTTO category groups → categories выбранной группы → attributes группы.
2. Создать draft с минимум одним variant.
3. Отдельно загрузить одно или несколько фото.
4. До submit редактировать через `PATCH /products/{id}/`.
5. Отправить на модерацию.

```json
{
  "title": "Wooden chair",
  "product_type": "Chair",
  "unit_price": "299.00",
  "currency": "EUR",
  "otto_category_id": 26822,
  "otto_category_group_id": 3593,
  "otto_attributes": {},
  "variants": [
    {
      "color_hex": "#5B91C8",
      "materials": ["Wood", "Fabric"],
      "width_cm": "50.00",
      "height_cm": "90.00",
      "length_cm": "55.00",
      "quantity": 3
    }
  ]
}
```

Правила:

- `product_type` — свободная строка. Мобильное приложение показывает локальные подсказки; backend не ведёт каталог типов.
- `unit_price` обязателен. `currency`: `TRY` по умолчанию, `EUR`, `USD`.
- `total_amount` отдаёт backend: `unit_price × total_quantity`.
- `color_hex` строго `#RRGGBB`; используйте color picker.
- `materials` — массив строк; первый материал основной для Kaufland.
- Размеры в сантиметрах и больше нуля; quantity — целое число больше нуля.
- OTTO attributes сейчас optional, но их значения проверяются по типу и allowed values из каталога.
- Seller не передаёт и не видит `ean_jv/ean_xl` до approve.
- Не вызывайте AI image processing, EAN, manager и marketplace endpoints из mobile.

## Контракт для web-разработчика

Web-панель использует JWT manager/admin. Ответ `403` означает недостаток роли, а не необходимость login как seller.

Нужные разделы интерфейса:

1. **Moderation list**: `/manager/products/`, фильтры, пагинация, карточка seller, variants, фото.
2. **Product card**: PATCH товара, source/generated images, moderation history, EAN после approve.
3. **Moderation**: approve/reject, сообщение seller, ручной availability request.
4. **EAN pool**: import, summary, list и предупреждение малого остатка.
5. **Marketplace preparation**: отдельные configurations для `product + marketplace + account`, AI draft и payload preview.
6. **Publications**: status, last error, OTTO MOIN/shop URL, job history, publish/update/state change выбранных targets.
7. **Managers**: создание manager-пользователей через protected endpoint. Admin так создать нельзя.

### Правила marketplace UI

- Configuration не меняет исходный товар seller. Она отдельная для каждого `marketplace/account`.
- Последовательность: configuration → payload preview → publish.
- Targets можно выбрать выборочно: `otto/jv`, `hood/xl` и т.д.
- Для карточки: `GET /orchestrator/products/{id}/publications/`; для общего списка: `GET /orchestrator/publications/`.
- Отображать job status (`queued`, `running`, `pending_confirmation`, `succeeded`, `partial`, `failed`) и publication status отдельно.
- AI-content не применяется автоматически: после генерации manager явно вызывает apply. `overwrite=false` заполняет только пустые поля, `true` заменяет существующий текст.
- Hood/Kaufland при deactivate удаляются. Их повторное включение — новый publish. OTTO поддерживает reversible deactivate → activate.

## API

Swagger: `/api/docs/` · Scalar: `/api/scalar/` · OpenAPI schema: `/api/schema/`.

В production Swagger, Scalar и schema должны закрываться Nginx Basic Auth/IP allowlist или отключаться. Это не заменяет JWT-защиту API.

### Technical

| Method | URL | Access | Назначение |
| --- | --- | --- | --- |
| `GET` | `/api/v1/health/` | public | Проверка API. |
| `GET` | `/api/schema/` | development public | OpenAPI document для Swagger/Scalar и генерации клиентов. |
| `GET` | `/api/docs/` | development public | Swagger UI. |
| `GET` | `/api/scalar/` | development public | Scalar UI. |

### Auth и profile

Префикс `/api/v1/auth/`.

| Method | URL | Access | Назначение |
| --- | --- | --- | --- |
| `POST` | `register/` | public | Регистрация seller, отправка email-code. |
| `POST` | `email/verify/` | public | Verify email-code, выдача JWT. |
| `POST` | `email/resend-verification/` | public | Повторная отправка кода. |
| `POST` | `login/` | public | Login username/password. |
| `POST` | `refresh/` | public | Обновление JWT-пары. |
| `POST` | `logout/` | authenticated | Blacklist refresh-token. |
| `GET` | `me/` | authenticated | Получить профиль. |
| `PATCH` | `me/` | authenticated | Частично изменить профиль. |

`PUT /auth/me/` удалён намеренно: profile редактируется только PATCH.

### Manager accounts

| Method | URL | Access | Назначение |
| --- | --- | --- | --- |
| `POST` | `/api/v1/manager/users/` | manager/admin | Создать manager account. |

### OTTO catalog и delivery

Префикс `/api/v1/catalog/`.

| Method | URL | Access | Назначение |
| --- | --- | --- | --- |
| `GET` | `otto/category-groups/` | authenticated | Группы local OTTO JSON-catalog. |
| `GET` | `otto/category-groups/{group_id}/categories/` | authenticated | Категории выбранной группы. |
| `GET` | `otto/category-groups/{group_id}/attributes/` | authenticated | Attributes выбранной группы. |
| `GET` | `otto/category-groups/tr/` | authenticated | Те же группы на турецком языке. |
| `GET` | `otto/category-groups/{group_id}/categories/tr/` | authenticated | Категории выбранной группы на турецком языке. |
| `GET` | `otto/category-groups/{group_id}/attributes/tr/` | authenticated | Атрибуты выбранной группы на турецком языке. |
| `GET` | `otto/shipping-profiles/?account=jv\|xl` | manager/admin | OTTO delivery profiles для аккаунта. |

### Products и images

Префикс `/api/v1/products/`.

| Method | URL | Access | Назначение |
| --- | --- | --- | --- |
| `GET` | `` | authenticated | Seller видит свои товары; manager/admin — все. Фильтры и pagination. |
| `POST` | `` | seller | Создать draft. |
| `GET` | `{id}/` | owner/manager/admin | Детали товара. |
| `PATCH` | `{id}/` | owner draft/rejected или manager/admin | Частично изменить товар/variants. |
| `DELETE` | `{id}/` | owner/manager/admin | Мягко архивировать допустимый товар. |
| `POST` | `{id}/availability/` | owner | Ответ на availability request. |
| `POST` | `{id}/withdraw/` | owner | Withdraw submitted/under_review. |
| `POST` | `{id}/deactivate/` | owner | Заявка manager-ам на деактивацию. |
| `POST` | `{id}/images/` | owner | Source image upload: multipart `image`, optional `is_primary`. |
| `POST` | `{id}/images/reorder/` | owner | Передать полный порядок image IDs. |
| `DELETE` | `{id}/images/{image_id}/` | owner | Удалить source image. |
| `POST` | `{id}/images/{image_id}/make-primary/` | owner | Сделать фото главным. |
| `POST` | `{id}/images/{image_id}/process/` | manager/admin | Запустить AI-photo processing. |

У товара одно `is_primary=true`: первое фото становится главным автоматически. Лимит — 10 JPEG/PNG/WebP фотографий до 10 MB каждая.

### Moderation

| Method | URL | Access | Назначение |
| --- | --- | --- | --- |
| `POST` | `/api/v1/products/{id}/submit/` | owner | Submit на модерацию. |
| `GET` | `/api/v1/products/{id}/moderation-history/` | owner/manager/admin | История решений. |
| `GET` | `/api/v1/manager/products/` | manager/admin | Менеджерский список товаров. |
| `POST` | `/api/v1/manager/products/{id}/approve/` | manager/admin | Approve и назначить EAN JV/XL. |
| `POST` | `/api/v1/manager/products/{id}/reject/` | manager/admin | Reject с комментарием. |
| `POST` | `/api/v1/manager/products/{id}/notifications/` | manager/admin | Отправить seller in-app + FCM notification. |
| `POST` | `/api/v1/manager/products/{id}/availability-request/` | manager/admin | Ручной запрос наличия; перезапускает таймер auto-reminder. |
| `POST` | `/api/v1/manager/products/{id}/deactivate/` | manager/admin | Legacy shortcut для снятия всех листингов. Новый UI использует `listing-state`. |

### Notifications и FCM

Префикс `/api/v1/notifications/`.

| Method | URL | Access | Назначение |
| --- | --- | --- | --- |
| `POST` | `devices/` | authenticated | Register/update FCM device token. |
| `POST` | `devices/deactivate/` | authenticated | Deactivate FCM token. |
| `GET` | `` | authenticated | Список своих in-app уведомлений. |
| `POST` | `read-all/` | authenticated | Прочитать все. |
| `POST` | `{id}/read/` | authenticated | Прочитать одно. |

### EAN pool

Префикс `/api/v1/manager/eans/`; все endpoints manager/admin.

| Method | URL | Назначение |
| --- | --- | --- |
| `GET` | `` | Пагинируемый pool. Query: `account=jv\|xl`, `is_assigned=true\|false`. |
| `POST` | `import/` | Import EAN строками: `{"account":"jv","codes":"..."}`. |
| `GET` | `summary/` | Свободные/назначенные EAN и low-stock warning. |

После первой успешной публикации EAN одноразовый: он не возвращается в пул, даже если листинг позднее удалён.

### Marketplace configuration, AI и jobs

Префикс `/api/v1/orchestrator/`; все write-операции и publication/configuration endpoints доступны manager/admin. Владелец товара может прочитать только свой marketplace job по его ID.

| Method | URL | Назначение |
| --- | --- | --- |
| `GET/PATCH` | `products/{id}/otto/{account}/configuration/` | OTTO configuration. |
| `GET` | `products/{id}/otto/{account}/payload-preview/` | OTTO payload preview. |
| `GET/PATCH` | `products/{id}/hood/{account}/configuration/` | Hood configuration. |
| `GET` | `products/{id}/hood/{account}/payload-preview/` | Hood payload preview. |
| `GET/PATCH` | `products/{id}/kaufland/{account}/configuration/` | Kaufland configuration. |
| `GET` | `products/{id}/kaufland/{account}/create-payload-preview/` | Kaufland create preview. |
| `GET` | `products/{id}/kaufland/{account}/update-payload-preview/` | Kaufland update preview. |
| `POST` | `products/{id}/ai-content/generate/` | Создать AI content draft для targets. |
| `GET` | `ai-content/generations/{generation_id}/` | Poll AI generation. |
| `POST` | `products/{id}/ai-content/generations/{generation_id}/apply/` | Применить AI draft к configurations. |
| `POST` | `products/{id}/listing-state/` | Semantic activate/deactivate выбранных листингов. |
| `GET` | `products/{id}/publications/` | Публикации товара. |
| `GET` | `publications/` | Общий список публикаций. Query: marketplace, account, status, product_id, page. |
| `GET` | `jobs/{job_id}/` | Статус marketplace job; доступ owner товара или manager/admin. |
| `POST` | `products/{id}/{operation}/` | Универсальная marketplace operation. |

`{account}`: `jv` или `xl`; marketplace: `otto`, `hood`, `kaufland`.

## Marketplace orchestration

Оркестратор изолирует особенности площадок от frontend: выбирает EAN/account, читает configuration, строит payload конкретной площадки, создаёт job, передаёт её в Celery и сохраняет результат в `MarketplacePublication`.

### Универсальная operation

```http
POST /api/v1/orchestrator/products/{product_pk}/{operation}/
Idempotency-Key: <new UUID>
```

Используйте только `targets`:

```json
{
  "targets": [
    {"marketplace": "otto", "account": "jv"},
    {"marketplace": "hood", "account": "xl"}
  ]
}
```

Swagger также показывает `channels`, `accounts`, `payloads` с `additionalProp`. Это legacy-поля для совместимости. Новый frontend использует только `targets`; payload строит backend.

| Operation | OTTO | Hood | Kaufland |
| --- | ---: | ---: | ---: |
| `search` | ✓ | ✓ | ✓ |
| `publish` | ✓ | ✓ | ✓ |
| `update` | ✓ | ✓ | ✓ |
| `delete` | — | ✓ | ✓ |
| `activate` | ✓ | — | — |
| `deactivate` | ✓ | — | — |

### `listing-state` для manager UI

```json
{
  "action": "deactivate",
  "targets": [
    {"marketplace": "otto", "account": "jv"},
    {"marketplace": "hood", "account": "jv"}
  ]
}
```

`action` — `deactivate` или `activate`. Без `targets` действие применяется ко всем подходящим публикациям товара.

- OTTO: reversible deactivate → activate.
- Hood/Kaufland: deactivate вызывает внешнее delete; чтобы включить снова, нужен новый publish.
- Ответ возвращает jobs и `unavailable_targets` для несовместимых целей.

## Локальный запуск

```powershell
Copy-Item .env.example .env
docker compose up -d --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
```

- Swagger: `http://localhost:8000/api/docs/`
- Scalar: `http://localhost:8000/api/scalar/`
- Django Admin: `http://localhost:8000/admin/`
- PostgreSQL с host: `5434`; Redis с host: `6380`.

Контейнеры используют внутренние `postgres:5432` и `redis:6379`. Host-порты нужны только для локальных инструментов Windows.

```powershell
docker compose ps
docker compose logs -f web
docker compose logs -f worker_marketplace
docker compose exec web python manage.py check
docker compose exec web python manage.py makemigrations --check --dry-run
docker compose exec web pytest -q
```

## `.env`

Начинайте с `.env.example`. Реальные секреты нельзя добавлять в Git или README.

| Группа | Примеры |
| --- | --- |
| Django | `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS` |
| PostgreSQL | `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT` |
| Redis/Celery | `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `REDIS_CACHE_URL` |
| FTP | `FTP_MEDIA_HOST`, `FTP_MEDIA_PORT`, `FTP_MEDIA_USERNAME`, `FTP_MEDIA_PASSWORD`, `FTP_MEDIA_REMOTE_ROOT`, `FTP_MEDIA_PUBLIC_BASE_URL`, `FTP_MEDIA_USE_TLS` |
| Image AI | `BULK_WHITE_IMAGE_SERVICE_URL`, `BULK_WHITE_IMAGE_SERVICE_TOKEN`, results URL и polling settings |
| OpenAI | `OPENAI_API_KEY`, `OPENAI_ENABLED`, model/timeout settings |
| Email SMTP | `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_SSL`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` |
| Firebase FCM | `FIREBASE_ENABLED`, `FIREBASE_SERVICE_ACCOUNT_FILE` |
| Marketplaces | OTTO/Hood/Kaufland URLs, endpoint paths, credentials, timeout/retry settings |

Перед production: `DJANGO_DEBUG=False`, HTTPS/Nginx/Gunicorn, backup PostgreSQL/Redis/FTP, мониторинг workers, rotation всех секретов и защита `/api/docs/`, `/api/scalar/`, `/api/schema/` через Basic Auth/IP allowlist.

## Тесты и структура

```text
config/                    settings, URLs, Celery
apps/
  accounts/                User, JWT, email verification, roles
  products/                products, variants, images, filters
  catalog/                 read-only OTTO catalog and delivery profiles
  moderation/              submit, approve/reject, history
  ean/                     import, allocation, consumption, summary
  notifications/           in-app notifications, FCM, tasks
  orchestrator/            jobs, publications, configurations, polling, AI
  marketplace/             OTTO, Hood, Kaufland builders/integration helpers
  idempotency/             duplicate-write protection
  common/                  permissions, throttles, HTTP/storage helpers
data/                      versioned OTTO JSON catalog
tests/
  unit/                    isolated domain and payload tests
  integration/             DRF, DB, permission and task tests
  e2e/                     seller → manager journeys
```

Внешние HTTP, FTP, FCM и OpenAI-вызовы в тестах замоканы или заблокированы. Pytest использует тестовую БД, не рабочие данные.

```powershell
docker compose exec web pytest -q
```
