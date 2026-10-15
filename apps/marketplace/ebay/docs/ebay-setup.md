# Авторизация eBay и уведомления об удалении аккаунта

На этом этапе реализовано подключение продавцов JV, XL и Depotum к eBay.
Публикация товаров и распределение EAN будут добавлены отдельно.
Ни один из этих эндпоинтов не удаляет аккаунт на eBay.

## Настройки сервера

Укажите следующие значения в окружении сервера. Не коммитьте секреты.

| Настройка | Stage | Прод |
| --- | --- | --- |
| `EBAY_ENVIRONMENT` | `sandbox` | `production` |
| `EBAY_PUBLIC_BASE_URL` | `https://stage.benimdepom.info` | `https://benimdepom.info` |
| App ID (OAuth Client ID) | `EBAY_SANDBOX_APP_ID` | `EBAY_PROD_APP_ID` |
| Cert ID (OAuth Client Secret) | `EBAY_SANDBOX_CERT_ID` | `EBAY_PROD_CERT_ID` |
| `EBAY_RUNAME` | OAuth RuName для Sandbox | OAuth RuName для Production |
| `EBAY_TOKEN_ENCRYPTION_KEY` | Отдельный ключ Fernet | Другой отдельный ключ Fernet |
| `EBAY_NOTIFICATION_VERIFICATION_TOKEN` | Случайный токен подтверждения | Другой случайный токен подтверждения |

`EBAY_CLIENT_ID` и `EBAY_CLIENT_SECRET` — внутренние настройки Django: они берутся
из переменных выбранного окружения. Добавлять их в `.env` не нужно.
Существующие `EBAY_SANDBOX_DEV_ID` и `EBAY_PROD_DEV_ID` можно оставить в `.env`;
текущая авторизация через REST OAuth не использует Dev ID.
Ключи приложения общие для JV, XL и Depotum. Каждый продавец проходит авторизацию
отдельно и получает собственные токены, которые сохраняются в системе.

Создайте ключ шифрования локально:

```sh
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Создайте токен подтверждения адреса уведомлений локально:

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Храните ключ шифрования в безопасном месте и сохраняйте его резервную копию.
Замена ключа сделает уже сохранённые токены нечитаемыми.
Токен подтверждения уведомлений — отдельное значение, а не Client Secret или токен доступа eBay.
Установите зависимости, примените миграции и выкатите бэкенд перед настройкой адресов в eBay.

## Настройка кабинета eBay Developer

Для Production откройте **Alerts & Notifications → Marketplace Account Deletion** и заполните:

- **Notification endpoint:** `https://benimdepom.info/api/v1/ebay/account-deletion/`
- **Verification token:** точное значение `EBAY_NOTIFICATION_VERIFICATION_TOKEN` на проде.
- **Alert email:** рабочий адрес компании, на котором отслеживаются уведомления.

При сохранении eBay автоматически отправляет проверочный GET-запрос с `challenge_code`.
К этому моменту эндпоинт должен быть доступен публично.
GET возвращает SHA-256 ответ на проверку и не меняет данные.
POST проверяет `X-EBAY-SIGNATURE` публичным ключом eBay, затем удаляет соответствующее
локальное подключение eBay и незавершённые попытки авторизации.
Пользователи BENIM, товары и данные остальных маркетплейсов не удаляются.
Содержимое уведомлений и пользовательские токены не сохраняются в логах или таблицах входящих уведомлений.
Повторное подлинное уведомление безопасно: если подключение уже отсутствует, удалять нечего.
После добавления хранения листингов и заказов eBay обработчик потребуется дополнить
удалением связанных с аккаунтом данных.

В разделе **User Tokens** создайте RuName с поддержкой OAuth.
В поля **Auth Accepted URL** и **Auth Declined URL** укажите:

- Sandbox: `https://stage.benimdepom.info/api/v1/ebay/oauth/callback/`
- Production: `https://benimdepom.info/api/v1/ebay/oauth/callback/`

Также укажите адрес опубликованной политики конфиденциальности BENIM.
Скопируйте идентификатор RuName в `EBAY_RUNAME`.
Именно этот идентификатор отправляется в параметре `redirect_uri`, а не адрес callback.

## Подключение продавцов

От имени авторизованного менеджера BENIM отправьте запрос:

```http
POST /api/v1/ebay/oauth/start/
Content-Type: application/json
Authorization: Bearer BENIM_MANAGER_ACCESS_TOKEN

{"account":"jv"}
```

Вместо `BENIM_MANAGER_ACCESS_TOKEN` подставьте токен доступа менеджера BENIM.
Откройте полученный `authorization_url` в браузере и разрешите доступ от соответствующего продавца eBay.
На stage используйте тестового продавца Sandbox, на проде — реального продавца.
Повторите авторизацию для `{"account":"xl"}` и `{"account":"depotum"}` с соответствующими продавцами.

Для определения неизменяемого ID продавца запрашивается разрешение
`https://api.ebay.com/oauth/api_scope/commerce.identity.readonly`.
Identity API использует домен `apiz.ebay.com` (Sandbox: `apiz.sandbox.ebay.com`);
запросы OAuth-токенов используют обычный домен `api`.
После изменения набора разрешений начните авторизацию заново через `/oauth/start/`:
обновление старой страницы callback не выдаст токен с новыми разрешениями.

Callback возвращает подтверждение подключения без раскрытия токенов.
Внутренняя функция `get_access_token` обновляет истекающие токены при обращении к ней;
её будет использовать будущий адаптер eBay.
`GET /api/v1/ebay/connections/` возвращает менеджеру список подключений и сроки действия токенов,
но не сами токены.
Одного пользователя eBay нельзя привязать к нескольким аккаунтам в одном окружении.

## Выполненные проверки

При реализации не вызывались публичный эндпоинт удаления, реальная авторизация
или тестовое уведомление из кабинета eBay.
Локальные тесты проверяли вычисление ответа на challenge, шифрование, проверку подписи
и одноразовый OAuth state с сохранением токенов. Ответы eBay были заменены вымышленными данными.

## Официальная документация

- [Авторизация Sell API](https://developer.ebay.com/develop/guides/sell/authorization)
- [Уведомления об удалении аккаунта](https://developer.ebay.com/develop/guides/sell/marketplace-user-account-deletion)
