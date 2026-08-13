# BENIM DEPOM — Backend

Backend для сервиса, в котором продавцы добавляют товары из мобильного приложения, а менеджеры проверяют их через web-панель. После одобрения товары будут передаваться в маркетплейсы через отдельные интеграции.

Проект — модульный Django-монолит. Он рассчитан на текущий масштаб в сотни пользователей с запасом до примерно 1 000 активных пользователей: PostgreSQL хранит бизнес-данные, Redis и Celery выполняют фоновые операции, а фотографии хранятся на FTP/FTPS.

## Что реализовано

- Регистрация продавца, JWT-авторизация, refresh/logout, профиль и выбор языка (`ru`, `tr`, `de`, `en`).
- Роли `seller`, `manager`, `admin` и разграничение доступа.
- Товары, варианты с размерами, количеством, HEX-цветом и текстовыми материалами.
- Загрузка, перестановка, удаление и выбор главной фотографии. Медиа сохраняются на FTP/FTPS.
- Модерация товаров: отправка, одобрение, отклонение и история решений.
- EAN-пул: импорт кодов для аккаунтов `jv` и `xl`, автоматическое резервирование двух кодов только при одобрении товара.
- Асинхронная AI-обработка фотографий: менеджер запускает генерацию, Celery ожидает результат и сохраняет варианты `white`, `interior`, `human` на FTP.
- Уведомления внутри приложения, FCM device tokens и серверная отправка Firebase Push.
- Напоминания о наличии товара: ежедневная автоматическая задача и ручной запрос менеджера.
- Отключение товара продавцом с уведомлением менеджеров.
- Swagger/OpenAPI, Django Admin, Docker Compose и базовые pytest-тесты.

## Роли и доступ

| Роль | Кто создаёт | Возможности |
| --- | --- | --- |
| `seller` | Публичная регистрация | Управляет только своими товарами, фото и профилем; отправляет товар на модерацию; подтверждает наличие, отзывает не одобренный товар или отключает одобренный; читает уведомления. |
| `manager` | Manager/admin web-панели или разработчик | Управляет товарами всех продавцов: может архивировать и отключать их, модерировать, импортировать EAN, отправлять уведомления, вручную запрашивать наличие, запускать AI-обработку фото и создавать других менеджеров. |
| `admin` | Только разработчик через терминал/Django Admin | Права менеджера плюс Django Admin. Публичного API для создания admin нет. |

Публичный `POST /api/v1/auth/register/` всегда создаёт только `seller`. Поле роли намеренно отсутствует: иначе любой пользователь мог бы стать менеджером.

В web-панели manager/admin создаёт менеджера через `POST /api/v1/manager/users/`. Этот endpoint всегда создаёт только роль `manager`, принять роль `admin` он не может.

Создать первого менеджера из Docker:

```powershell
docker compose exec web python manage.py shell -c "from apps.accounts.models import User; User.objects.create_user(username='manager1', password='CHANGE_ME', role=User.Role.MANAGER)"
```

Для доступа к Django Admin нужен пользователь с `is_staff=True`; стандартный способ создать такого пользователя:

```powershell
docker compose exec web python manage.py createsuperuser
```

Админка доступна по `http://localhost:8000/admin/`.

## Основной поток товара

```text
Продавец создаёт черновик
  → добавляет варианты и фотографии
  → отправляет на модерацию
  → менеджер при необходимости запускает AI-обработку фото
  → менеджер одобряет или отклоняет товар
  → при одобрении резервируются EAN JV + EAN XL
  → продавец получает in-app / push-уведомление.
```

AI-генерацию не запускает продавец и не видит её технические детали. Менеджер видит статус, ошибку и сгенерированные изображения. Продавец видит только своё исходное фото.

### Статусы товара

| Статус | Значение |
| --- | --- |
| `draft` | Черновик. Продавец может редактировать товар, варианты и фотографии. |
| `submitted` | Товар отправлен менеджеру на модерацию. |
| `under_review` | Зарезервирован для будущего явного этапа проверки. |
| `approved` | Товар одобрен. Для него включаются напоминания о наличии. |
| `rejected` | Товар отклонён. Продавец может исправить его и отправить повторно. |
| `deactivated` | Менеджер подтвердил заявку продавца на отключение ранее одобренного товара. В будущем этот статус будет запускать снятие с маркетплейсов. |
| `archived` | Мягко удалён. Не показывается в рабочих списках, история остаётся в базе. |

## Стек

- Python 3.12, Django 5.2, Django REST Framework
- PostgreSQL 16
- Redis 7, Celery 5 и Celery Beat
- SimpleJWT с rotation и blacklist refresh-токенов
- drf-spectacular / Swagger UI
- Firebase Admin SDK для FCM Push и проверки Firebase ID token
- FTP/FTPS storage для фотографий
- Pillow и `requests`
- pytest, pytest-django, Ruff
- Docker / Docker Compose

## Быстрый запуск через Docker

### 1. Подготовить `.env`

```powershell
Copy-Item .env.example .env
```

Заполни обязательные переменные: PostgreSQL, FTP, Firebase (если нужны push/SMS) и AI-сервис. Не коммить `.env` и Firebase service-account JSON в Git.

### 2. Запустить сервисы и миграции

```powershell
docker compose up -d --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
```

Docker запускает пять сервисов:

- `web` — Django API на `http://localhost:8000`;
- `worker` — Celery: push, AI, другие фоновые задачи;
- `beat` — планировщик ежедневных задач;
- `postgres` — доступен с хоста на порту `5434`;
- `redis` — доступен с хоста на порту `6380`.

Контейнеры сами используют внутренние имена `postgres:5432` и `redis:6379`; не меняй их на host-порты в `docker-compose.yml`.

Полезные команды:

```powershell
docker compose ps
docker compose logs -f web
docker compose logs -f worker
docker compose exec web python manage.py check
docker compose exec web pytest
```

Полезные страницы:

- Swagger: `http://localhost:8000/api/docs/`
- OpenAPI schema: `http://localhost:8000/api/schema/`
- Django Admin: `http://localhost:8000/admin/`
- Healthcheck: `http://localhost:8000/api/v1/health/`

## Авторизация

### JWT

| Значение | Срок жизни | Назначение |
| --- | --- | --- |
| `access` | 15 минут | Передаётся в каждом защищённом API-запросе. |
| `refresh` | 7 дней | Получает новый access через endpoint refresh. |

Для защищённого запроса передавай:

```http
Authorization: Bearer <access_token>
```

После `POST /api/v1/auth/logout/` refresh-токен добавляется в blacklist и больше не должен использоваться. При refresh включена ротация: клиент сохраняет новый refresh из ответа и заменяет старый. Мобильное приложение хранит оба токена в защищённом хранилище устройства.

### Firebase Phone Auth / SMS

Firebase не является Django Admin и не заменяет JWT. Firebase выполняет SMS-подтверждение телефона на устройстве; мобильный Firebase SDK после успешного SMS получает Firebase ID token. Затем приложение вызывает backend:

```http
POST /api/v1/auth/phone/verify/
Authorization: Bearer <access_token>

{
  "id_token": "firebase_id_token"
}
```

Backend проверяет токен через Firebase Admin SDK, сохраняет телефон и ставит `is_phone_verified=true`.

Сейчас пароль обязателен, а JWT login ещё не блокируется для неподтверждённого номера. Обязательный второй фактор включается отдельно после стабильной интеграции Firebase SDK в мобильном приложении.

## Общие правила API

Все прикладные маршруты начинаются с `/api/v1/`. Формат — JSON, кроме загрузки изображений (`multipart/form-data`).

Swagger показывает актуальную схему и позволяет выполнять запросы вручную. Для Swagger сначала вызови login, затем нажми **Authorize** и вставь `Bearer <access_token>`.

Endpoints в Swagger разделены по тегам: `Mobile — Authentication`, `Authentication — Shared`, `Catalog`, `Products`, `Moderation`, `Web — Moderation`, `Web — EAN pool`, `Web — Manager accounts`, `Notifications` и `Service`.

У каждого endpoint есть короткий summary. Схемы в разделе `Schemas` используют доменные префиксы: `Auth…`, `Catalog…`, `Products…`, `Moderation…`, `Ean…`, `Notifications…`, `Web…`. Стандартный Swagger UI не поддерживает вложенные папки для schemas, поэтому префиксы — совместимый способ собрать их рядом в списке.

Списки используют стандартную пагинацию DRF: 20 объектов на страницу.

```json
{
  "count": 42,
  "next": "http://localhost:8000/api/v1/products/?page=2",
  "previous": null,
  "results": []
}
```

## API: авторизация и профиль

| Метод | URL | Доступ | Назначение |
| --- | --- | --- | --- |
| `POST` | `/auth/register/` | публично | Регистрация продавца. |
| `POST` | `/auth/login/` | публично | Login по username и паролю, возвращает `access` и `refresh`. |
| `POST` | `/auth/refresh/` | публично | Обновить JWT-пару по refresh. |
| `POST` | `/auth/logout/` | авторизован | Отозвать refresh-токен. |
| `GET/PATCH` | `/auth/me/` | авторизован | Получить или изменить профиль. |
| `POST` | `/auth/phone/verify/` | авторизован | Подтвердить телефон Firebase ID token. |

Полный префикс для таблицы: `/api/v1/auth/`.

Пример регистрации:

```json
{
  "username": "seller1",
  "email": "seller@example.com",
  "password": "StrongPassword123!",
  "password_confirm": "StrongPassword123!",
  "phone": "+77000000000",
  "preferred_language": "ru"
}
```

## API: каталог и товары

### Каталог

Полный префикс: `/api/v1/catalog/`.

| Метод | URL | Доступ | Назначение |
| --- | --- | --- | --- |
| `GET` | `/categories/` | публично | Активные категории. |
| `POST` | `/categories/` | manager/admin | Создать категорию. |
| `GET/PATCH/DELETE` | `/categories/{id}/` | manager/admin | Управлять категорией. |

В текущем API нет endpoints `catalog/colors` и `catalog/materials`: они были нужны старой модели и убраны из публичного контракта. Цвет и материалы приходят прямо в варианте товара как `color_hex` и `materials`.

`product_type` и `category` — не одно и то же:

| Поле | Обязательно | Что означает | Пример для стула |
| --- | --- | --- | --- |
| `product_type` | да | Свободная строка от продавца. Мобильное приложение показывает подсказки на его языке, но backend не ограничивает список. Используется в AI-сервисе как тип (`produktart`) и позднее будет маппиться в типы маркетплейсов. | `Chair`, `Стул`, `Kanepe` |
| `category` | нет | Более широкая бизнес-группа для навигации и фильтрации. Сейчас она плоская, без дерева. | `Living room furniture` или `Home furniture` |

### Товары

Полный префикс: `/api/v1/products/`.

| Метод | URL | Доступ | Назначение |
| --- | --- | --- | --- |
| `GET` | `/` | авторизован | Seller видит свои товары, manager/admin — все. |
| `POST` | `/` | seller | Создать товар в `draft`. |
| `GET/PATCH/PUT/DELETE` | `/{id}/` | владелец или manager/admin | Детали, изменение и мягкое удаление. Seller изменяет/удаляет только свои `draft/rejected`; manager/admin — любые. |
| `POST` | `/{id}/availability/` | владелец | Ответить на запрос наличия: `{"is_available": true}` или `false`. |
| `POST` | `/{id}/withdraw/` | владелец | Отозвать `submitted/under_review` товар. Он архивируется без уведомления менеджеров. |
| `POST` | `/{id}/deactivate/` | владелец | Создать заявку на деактивацию одобренного товара. Возвращает `202`; товар остаётся `approved`, а менеджеры получают уведомление. |
| `POST` | `/{id}/images/` | владелец | Загрузить исходную фотографию. |
| `POST` | `/{id}/images/reorder/` | владелец | Указать новый порядок всех фотографий. |
| `DELETE` | `/{id}/images/{image_id}/` | владелец | Удалить фотографию. |
| `POST` | `/{id}/images/{image_id}/make-primary/` | владелец | Сделать фото главным. |
| `POST` | `/{id}/images/{image_id}/process/` | manager/admin | Запустить AI-обработку после отправки товара на модерацию. |

Пример создания товара:

```json
{
  "title": "Wooden chair",
  "product_type": "Wooden chair",
  "category": null,
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

Правила варианта:

- `color_hex` — строго `#RRGGBB`;
- `materials` — одна или две непустые уникальные строки, до 100 символов каждая;
- размеры передаются в сантиметрах и должны быть больше нуля;
- количество — целое число от 1;
- `total_quantity` рассчитывает backend как сумму `quantity` всех вариантов.

Загрузка изображения — `multipart/form-data` с полями `image` и необязательным `is_primary`. Разрешены JPEG, PNG и WebP, максимум 10 MB и 10 фотографий на товар.

`is_primary=true` означает «главная фотография товара»: она должна показываться первой в карточке и позднее уйдёт основной фотографией в маркетплейсы. У товара может быть только одно главное фото. Первое загруженное фото автоматически становится главным; загрузка нового с `is_primary=true` снимает этот признак с предыдущего. Его также можно сменить отдельным endpoint `make-primary/`.

Фильтры списков товаров и менеджерского списка: `search`, `status`, `product_type`, `category`, `color_hex`, `material`, `is_available`, `ordering`, `page`.

Пример: `/api/v1/products/?status=approved&color_hex=%235B91C8&ordering=-updated_at`.

## API: модерация и менеджер

Полный префикс: `/api/v1/`.

| Метод | URL | Доступ | Назначение |
| --- | --- | --- | --- |
| `POST` | `/products/{id}/submit/` | владелец | Отправить `draft/rejected` на модерацию. Нужны вариант и минимум одно фото. |
| `GET` | `/products/{id}/moderation-history/` | владелец или manager/admin | История решений. |
| `GET` | `/manager/products/` | manager/admin | Все рабочие товары с фильтрами. |
| `POST` | `/manager/products/{id}/approve/` | manager/admin | Одобрить товар; `comment` необязателен. Перед сменой статуса резервируются свободные EAN JV и XL. |
| `POST` | `/manager/products/{id}/reject/` | manager/admin | Отклонить товар; `comment` обязателен. |
| `POST` | `/manager/products/{id}/notifications/` | manager/admin | Отправить продавцу уведомление: `title` и обязательный `body`. |
| `POST` | `/manager/products/{id}/availability-request/` | manager/admin | Вручную запросить наличие. Новый автоматический срок начнётся от этого момента. |
| `POST` | `/manager/products/{id}/deactivate/` | manager/admin | Подтвердить ожидающую заявку продавца на деактивацию. После этого статус станет `deactivated`, а продавец получит уведомление. |

При успешном approve товар получает ровно два EAN: один с аккаунтом `jv`, второй — с `xl`. Коды остаются в EAN-пуле для контроля и аудита, а их значения одновременно записываются прямо в поля товара `ean_jv` и `ean_xl`. Поэтому manager/admin получает оба значения вместе с товаром одним запросом. До одобрения поля пустые; seller их не получает в API-ответах. Если в одном из пулов нет свободного кода, approve вернёт ошибку и товар останется `submitted`.

## API: управление менеджерами

Полный префикс: `/api/v1/manager/users/`.

| Метод | URL | Доступ | Назначение |
| --- | --- | --- | --- |
| `POST` | `/` | manager/admin | Создать нового пользователя с ролью `manager`. Создание `admin` этим API запрещено. |

Пример запроса:

```json
{
  "username": "manager2",
  "password": "StrongPassword123!",
  "password_confirm": "StrongPassword123!",
  "email": "manager@example.com",
  "preferred_language": "ru"
}
```

## API: EAN-пул

Полный префикс: `/api/v1/manager/eans/`. Все endpoints доступны manager/admin.

| Метод | URL | Назначение |
| --- | --- | --- |
| `GET` | `/` | Список EAN. Фильтры: `account=jv|xl`, `is_assigned=true|false`. |
| `POST` | `/import/` | Пакетный импорт EAN для одного аккаунта. |
| `GET` | `/summary/` | Свободные EAN по JV/XL и предупреждение о малом остатке. |

Пример импорта:

```json
{
  "account": "jv",
  "codes": "4071489789737\n4071489789744\n4071489789751"
}
```

В Swagger внутри JSON используй `\n`, а не реальный Enter внутри строки. В будущем web-панель будет передавать содержимое обычного многострочного поля сама.

Коды проверяются как GTIN/EAN-8/12/13/14, дубликаты не создаются. Ответ содержит число добавленных, уже существующих, повторов во входе и некорректных кодов. Порог низкого остатка задаётся `EAN_LOW_STOCK_THRESHOLD`.

## API: уведомления

Полный префикс: `/api/v1/notifications/`.

| Метод | URL | Доступ | Назначение |
| --- | --- | --- | --- |
| `POST` | `/devices/` | авторизован | Сохранить/обновить FCM registration token: `token`, `platform` (`android`/`ios`). |
| `POST` | `/devices/deactivate/` | авторизован | Отключить token при logout или смене устройства. |
| `GET` | `/` | авторизован | Список собственных in-app уведомлений. |
| `POST` | `/{id}/read/` | авторизован | Прочитать одно уведомление. |
| `POST` | `/read-all/` | авторизован | Прочитать все уведомления. |

Push отправляется асинхронно Celery worker-ом. Если Firebase выключен, уведомление всё равно сохраняется в базе; push пропускается.

### Напоминание о наличии

Celery Beat каждый день в 10:00 UTC ищет одобренные товары, которым пора запросить наличие. Срок по умолчанию — 14 дней:

- после одобрения;
- после ручного запроса менеджера;
- после ответа продавца о наличии.

Продавец отвечает через `/products/{id}/availability/`. Если товар больше не продаётся, он вызывает `/products/{id}/deactivate/`: создаётся заявка, а не немедленное отключение. Менеджер подтверждает её через `/manager/products/{id}/deactivate/`; только после этого товар становится `deactivated` и продавец получает уведомление об успехе.

## AI-обработка изображений

Менеджер вызывает:

```text
POST /api/v1/products/{product_id}/images/{image_id}/process/
```

Товар должен быть в одном из статусов `submitted`, `under_review`, `approved`. Endpoint быстро возвращает `202 Accepted`; тяжёлая операция выполняется worker-ом.

1. Backend отправляет исходную фотографию, название и тип товара во внешний AI-сервис.
2. Внешний сервис возвращает `queued`, `product_id` и `status_url`.
3. Celery опрашивает `status_url` раз в `BULK_WHITE_IMAGE_SERVICE_POLL_INTERVAL_SECONDS` секунд.
4. После `completed` backend скачивает `white`, `interior`, `human` и сохраняет их на FTP.
5. В `ProductImage` становится `processing_status=succeeded`; белый вариант записывается в `processed_image`, все варианты — в `generated_images`.

Статусы обработки: `pending`, `processing`, `succeeded`, `failed`. При ошибке описание сохраняется в `processing_error`; при успехе и ошибке технический ответ хранится в `processing_result`.

Технические поля AI и готовые варианты изображений видит только manager/admin. Seller получает исходное изображение без статуса генерации.

## Инструкция для мобильного разработчика

1. Зарегистрировать seller или выполнить login.
2. Безопасно сохранить `access` и `refresh`.
3. Передавать `Authorization: Bearer <access>`.
4. При `401` вызвать refresh; если refresh невалиден — выйти из аккаунта.
5. Для формы товара показать локальные подсказки по строке `product_type`; при необходимости загрузить `/catalog/categories/`.
6. Отправлять цвет в `color_hex` из color picker, а материалы — строками в `materials`.
7. Создать товар JSON-запросом, затем загрузить фото отдельными multipart-запросами.
8. Отправить товар на модерацию. Не запускать AI из мобильного клиента.
9. После login и при изменении токена вызвать `/notifications/devices/`.
10. После Firebase SMS-подтверждения передать Firebase ID token в `/auth/phone/verify/`.

## Инструкция для web-разработчика

Менеджерская панель должна использовать login с ролью `manager` или `admin` и строиться на этих блоках:

- список товаров `/manager/products/` с фильтрами и пагинацией;
- карточка товара с оригинальными и AI-фотографиями;
- approve/reject с комментарием;
- кнопка запуска AI-обработки конкретного фото;
- кнопка ручного запроса наличия;
- отправка уведомления продавцу;
- EAN-страница: импорт многострочного списка, список кодов, фильтр занятых/свободных и предупреждение `requires_attention` из summary;
- управление категориями, а пока создание типов товара удобно выполнять из Django Admin.

## Языки и тексты товаров

Четыре языка интерфейса (`ru`, `tr`, `de`, `en`) — это задача mobile/web-клиентов: они хранят переводы кнопок, экранов, ошибок и подписей у себя через i18n. Backend уже хранит `preferred_language` пользователя, чтобы позднее выбирать язык системных уведомлений.

Пользовательский текст — название товара, материалы, описание и будущие поля — backend хранит в оригинальном Unicode-виде и не переводит сам. Поэтому турецкий продавец может отправить турецкое название, и менеджер сначала увидит именно турецкий оригинал. Продавцу не нужно вручную заполнять четыре версии текста.

Правильный следующий этап после получения контрактов маркетплейсов: хранить оригинал как источник, определить язык ввода (явно от клиента или автоопределением), а переводы/описания для конкретной площадки генерировать по требованию через AI и сохранять как отдельные версии. Так не будет лишних четырёх полей у каждого товара и не потеряется исходный текст продавца.

## Переменные окружения

Начни с `.env.example`. Ниже перечислены обязательные группы переменных; реальные секреты никогда не добавляются в README или Git.

```env
DJANGO_SECRET_KEY=replace_me
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1

POSTGRES_DB=marketplace
POSTGRES_USER=marketplace_user
POSTGRES_PASSWORD=change_me
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5434

CELERY_BROKER_URL=redis://127.0.0.1:6380/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6380/1

FTP_MEDIA_HOST=
FTP_MEDIA_PORT=21
FTP_MEDIA_USERNAME=
FTP_MEDIA_PASSWORD=
FTP_MEDIA_REMOTE_ROOT=/api-media
FTP_MEDIA_PUBLIC_BASE_URL=https://your-domain.example/api-media
FTP_MEDIA_USE_TLS=true
FTP_MEDIA_PASSIVE_MODE=true

FIREBASE_ENABLED=false
FIREBASE_SERVICE_ACCOUNT_FILE=secrets/firebase-service-account.json

PRODUCT_AVAILABILITY_REMINDER_DAYS=14
PRODUCT_AVAILABILITY_REMINDER_BATCH_SIZE=500
EAN_LOW_STOCK_THRESHOLD=20

BULK_WHITE_IMAGE_SERVICE_URL=
BULK_WHITE_IMAGE_SERVICE_TOKEN=
BULK_WHITE_IMAGE_SERVICE_TIMEOUT_SECONDS=120
BULK_WHITE_IMAGE_SERVICE_RESULTS_URL=https://hiw-gen.automatonsoft.de/api/generation-results/{product_id}/
BULK_WHITE_IMAGE_SERVICE_POLL_INTERVAL_SECONDS=10
BULK_WHITE_IMAGE_SERVICE_MAX_POLL_ATTEMPTS=60
```

При запуске через Docker Compose значения PostgreSQL/Redis для контейнеров переопределяются самим compose-файлом. Пример выше нужен для команд, запускаемых с Windows-хоста.

## Структура проекта

```text
config/                 settings, URLs, Celery app
apps/
  accounts/             custom User, JWT, профиль, Firebase phone verification
  catalog/              категории и типы товара
  products/             товары, варианты, фото, FTP URLs, фильтрация
  moderation/           submit, approve/reject, история решений
  notifications/        in-app notifications, FCM tokens, Celery tasks
  ean/                  импорт, резервирование и контроль EAN-кодов
  common/               permissions, FTP storage, AI service client
secrets/                локальный Firebase service-account JSON, исключён из Git
```

## Проверки

```powershell
docker compose exec web python manage.py check
docker compose exec web python manage.py makemigrations --check --dry-run
docker compose exec web pytest
```

Тесты находятся в корне проекта: `tests/unit`, `tests/integration` и `tests/e2e`.
Они покрывают 49 критичных сценариев авторизации и JWT-blacklist, товаров и фото,
ролей, модерации, EAN-пула, уведомлений, Firebase, Celery-задач, AI-обработки
и полных путей «продавец → менеджер». Внешние HTTP/FTP/FCM-вызовы в тестах
заблокированы или замоканы; pytest использует только временную тестовую БД.

## Что ещё предстоит

- Интеграции с маркетплейсами: создание, обновление, снятие с публикации, статусы и ошибки. Реализуются после получения их API-контрактов.
- OpenAI-генерация названий и описаний под формат каждой площадки.
- Обязательный второй фактор при login после завершения Firebase-интеграции на мобильной стороне.
- Чат менеджер ↔ продавец не планируется: текущая коммуникация остаётся через in-app/push-уведомления и запрос наличия товара.
- Production-развёртывание: Gunicorn, Nginx, HTTPS, CI/CD, Sentry/мониторинг, резервные копии PostgreSQL и FTP-медиа.
