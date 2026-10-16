## Главное для коллеги

Сейчас у них не завершена подготовка аккаунта продавца: OAuth и SELLING_POLICY_MANAGEMENT уже работают, но sellerRegistrationCompleted=false, отсутствуют политики и склады.

У нас подтверждена работа с Production. Подтверждённого прохождения Sandbox от регистрации продавца до публикации я не нашёл. Поэтому ниже Sandbox-инструкция отдельно помечена как рекомендация по документации, а не наш личный опыт.

Изучил код, тесты, runbook и выполнил чтение текущих настроек eBay из prod. Файлы и настройки аккаунтов не менял. ebay-crud.md внутри этого репозитория не обнаружен.

---

## 1. Что удалось подтвердить

Использую четыре обозначения:

- Код — реализовано в проекте; само по себе не доказывает выполнение операции.
- Prod-проверка — получено сейчас из реального API eBay.
- Документация — рекомендуемый порядок из официальных источников.
- Не установлено — подтверждения нет.

### Текущий Production

Чтением API 16.10.2026 получено:

| Проверка | JV | XL | DEP |
|---|---:|---:|---:|
| sellerRegistrationCompleted | true | true | true |
| Fulfillment policies | 5 | 22 | 19 |
| Payment policies | 1 | 3 | 2 |
| Return policies | 1 | 8 | 9 |
| Inventory locations | 17 | 1 | 1 |

Источник — реальные ответы:


GET /sell/account/v1/privilege
GET /sell/account/v1/fulfillment_policy?marketplace_id=EBAY_DE
GET /sell/account/v1/payment_policy?marketplace_id=EBAY_DE
GET /sell/account/v1/return_policy?marketplace_id=EBAY_DE
GET /sell/inventory/v1/location


Важное ограничение: наличие этих ресурсов не доказывает, кто и когда создал их вручную, через WareHub или другим инструментом.

У DEP также получен существующий offer с listingId, но сейчас он `UNPUBLISHED`, а локальная запись — withdrawn. Это нельзя представлять как подтверждение действующего объявления.

---

## 2. Приложение eBay Developer

### Что нужно настроить отдельно для каждого окружения

| Параметр | Sandbox | Production |
|---|---|---|
| Keyset | Sandbox Client ID / Secret | Production Client ID / Secret |
| REST API | https://api.sandbox.ebay.com | https://api.ebay.com |
| OAuth authorization | https://auth.sandbox.ebay.com/oauth2/authorize | https://auth.ebay.com/oauth2/authorize |
| Token endpoint | https://api.sandbox.ebay.com/identity/v1/oauth2/token | https://api.ebay.com/identity/v1/oauth2/token |
| Продавец | Тестовый Sandbox user | Реальный продавец |
| RuName | Для Sandbox | Для Production |

Код: выбор API по EBAY_API_ENV реализован в services/database-service/ebay_service/client.py:49.

Наши основные настройки:


EBAY_API_ENV=sandbox
EBAY_CLIENT_ID=<ключ выбранного окружения>
EBAY_CLIENT_SECRET=<секрет выбранного окружения>
EBAY_OAUTH_RU_NAME=<RuName выбранного окружения>
EBAY_TOKEN_ENCRYPTION_KEY=<ключ шифрования refresh tokens>


### RuName, accepted и declined URLs

Документация: в настройках User Tokens / Redirect URLs необходимо задать:

- название приложения;
- Privacy Policy URL;
- Auth Accepted URL — обработчик успешного согласия;
- Auth Declined URL — страница отказа;
- соответствующий RuName.

В OAuth-параметр redirect_uri передаётся RuName, а не обычный callback URL. Sandbox и Production используют разные RuName. [Официальная настройка RuName](https://developer.ebay.com/develop/guides/sell/authorization).

Код: наш успешный callback:


GET /api/v1/ebay/oauth/callback/?code=...&state=...


Не установлено: точные accepted/declined URLs, внесённые вручную в Developer Portal. Наличие callback в коде не доказывает его точное значение в портале.

### Scopes

Код: при авторизации запрашиваем:


https://api.ebay.com/oauth/api_scope/sell.inventory
https://api.ebay.com/oauth/api_scope/sell.account


Источник: services/database-service/ebay_service/client.py:202.

Для описанного Inventory CRUD и настройки политик у коллеги уже есть нужные основные scopes. Дополнительные функции — например, обработка заказов — потребуют отдельной проверки разрешений.

### Уведомления об удалении аккаунта
Документация: перед использованием нового Production keyset необходимо настроить Marketplace Account Deletion notifications либо пройти допустимую процедуру exemption. Просто поставить фиктивный URL недостаточно. [Требования активации Production keyset](https://developer.ebay.com/develop/guides/sell/marketplace-user-account-deletion).

Код: у нас есть:


GET  /api/v1/ebay/notifications/marketplace-account-deletion/
POST /api/v1/ebay/notifications/marketplace-account-deletion/


GET обрабатывает challenge, POST проверяет подпись и тему уведомления.

Ограничение нашей реализации: валидное уведомление подтверждается ответом 204, но фактическое удаление пользовательских данных этот обработчик не выполняет. Нельзя выдавать это за полностью законченный процесс обработки удаления.

Источник: services/database-service/ebay_service/views.py:57.

---

## 3. Аккаунт продавца и sellerRegistrationCompleted=false

### Что подтверждено у нас

- Сейчас реальные JV, XL и DEP возвращают sellerRegistrationCompleted=true.
- Код не создаёт eBay-пользователя и не завершает регистрацию продавца.
- Записи о том, какие именно формы регистрации, банковские данные и проверки владельцы проходили вручную, не найдены.
- Наши Sandbox-тесты используют mock-ответы, а не доказывают реальную активацию Sandbox-продавца.

Источник тестов: services/database-service/ebay_service/tests.py:528.

### Что делать коллеге в Sandbox

Документация и рекомендуемый порядок:

1. Проверить, что OAuth подключил именно Sandbox-пользователя.
2. Проверить, что это тестовый seller, а не только buyer.
3. Для контрольной проверки создать нового продавца через Sandbox User Registration Tool в Developer Portal.
4. Войти в Sandbox с полным именем пользователя, включая TESTUSER_, если этот префикс присвоен eBay.
5. Подключить этого продавца через Sandbox OAuth.
6. Повторить:


GET https://api.sandbox.ebay.com/sell/account/v1/privilege
Authorization: Bearer <SANDBOX_USER_ACCESS_TOKEN>


Официальная поддержка указывает, что новые Sandbox test users поддерживают Managed Payments. Это не означает, что наш проект лично прошёл и проверил такой onboarding. [Создание Sandbox users и Managed Payments](https://developer.ebay.com/support/developer-account-support).

### Важное предупреждение про старые инструкции

В старой статье eBay встречается совет вызвать ValidateTestUserRegistration.

Не использовать его: метод отключён 30 января 2025 года. Это прямо указано в release notes. [Официальное уведомление об отключении](https://developer.ebay.com/devzone/xml/docs/releasenotes.html).

Если новый Sandbox seller после корректного подключения продолжает возвращать false, следующий шаг — обращение в Developer Support с окружением, test username, временем и обезличенным ответом API. Рабочего актуального «API-вызова, который принудительно ставит этот флаг в true», в нашем проекте нет.

### Seller Hub, Managed Payments, профиль

- Код: наш проект не включает Seller Hub и не проверяет его как отдельный флаг готовности.
- Документация: регистрация продавца должна быть завершена; getPrivileges используется для проверки активности и лимитов. [Проверка регистрации и лимитов](https://developer.ebay.com/api-docs/sell/static/seller-accounts/ht_get-selling-limits.html).
- Рекомендация для Production: владелец проходит реальные запросы eBay по данным продавца и выплатам. Sandbox не заменяет эти проверки.
- Не установлено: какие конкретные шаги Managed Payments и заполнения профиля проходили наши владельцы аккаунтов.

Включение Business Policies не равно завершению регистрации продавца. Это отдельные проверки.

---

## 4. OAuth: наш реализованный путь

### Подключение

Код:


GET /api/v1/ebay/oauth/authorization-url/?account=jv


WareHub возвращает authorization URL. Далее продавец вручную входит в eBay и подтверждает доступ.

Callback:


GET /api/v1/ebay/oauth/callback/?code=...&state=...


Внешний обмен:
POST /identity/v1/oauth2/token
Authorization: Basic <client_id:client_secret в Base64>
Content-Type: application/x-www-form-urlencoded



grant_type=authorization_code
code=<полученный code>
redirect_uri=<RuName>


state у нас подписан, содержит аккаунт и проверяется с ограничением 600 секунд.

Источники: services/database-service/ebay_service/client.py:220, services/database-service/ebay_service/views.py:845.

### Хранение

Код: refresh token сохраняется зашифрованным Fernet в EbayOAuthCredential. В ответ браузеру токен не возвращается.

Есть fallback на:


EBAY_JV_REFRESH_TOKEN
EBAY_XL_REFRESH_TOKEN
EBAY_DEP_REFRESH_TOKEN


если записи в БД нет.

Источник: services/database-service/ebay_service/credentials.py:12.

Рекомендация: Sandbox и Production должны иметь раздельное хранилище токенов. Наша модель идентифицирует запись по account, без поля окружения; переключение одного экземпляра между Sandbox и Production требует осторожности.

### Обновление access token

Код: перед продавцовыми операциями выполняется:


POST /identity/v1/oauth2/token



grant_type=refresh_token
refresh_token=<сохранённый refresh token>


Источник: services/database-service/ebay_service/client.py:898.

### Что происходит при 401

Не стоит приписывать нам лишнее: в проверенном клиенте нет специального автоматического повторения запроса после 401. Ошибка передаётся выше с контекстом операции.

Рекомендация коллеге:

1. Проверить соответствие токена окружению и приложению.
2. Обновить access token и повторить безопасный запрос.
3. Если refresh token отклонён или доступ отозван — повторить пользовательский OAuth consent.
4. Не повторять создание offer вслепую после неопределённого результата.

Подтверждённой истории нашего исправления конкретного 401 Invalid access token я не нашёл.

---

## 5. Business Policies: порядок и реальные настройки

### 5.1. Включение

Код:


POST /sell/account/v1/program/opt_in



{
  "programType": "SELLING_POLICY_MANAGEMENT"
}


Наш endpoint:


POST /api/v1/ebay/seller/programs/selling-policy-management/



{
  "account": "jv"
}


Источник: services/database-service/ebay_service/client.py:314.

Коллега этот этап уже выполнил. Для контроля:


GET /sell/account/v1/program/get_opted_in_programs


### 5.2. Создание политик

Методы:


POST /sell/account/v1/fulfillment_policy
POST /sell/account/v1/payment_policy
POST /sell/account/v1/return_policy


Общие заголовки:


Authorization: Bearer <USER_ACCESS_TOKEN>
Content-Type: application/json


Наш wrapper:


POST /api/v1/ebay/seller/policies/



{
  "account": "jv",
  "policy_type": "fulfillment",
  "policy": {
    "...": "данные политики"
  }
}


Код: проверяет наличие name, marketplaceId и массива categoryTypes; остальную структуру передаёт eBay. Это не полная локальная проверка всех требований eBay.

Источник: services/database-service/ebay_service/views.py:160.

После создания сохраняются возвращённые fulfillmentPolicyId, paymentPolicyId, returnPolicyId. Создавать и редактировать политики также можно в My eBay. [Business Policies](https://developer.ebay.com/api-docs/user-guides/static/trading-user-guide/business-policies.html).

### 5.3. Какие условия реально найдены

Prod-проверка: для существующего DEP offer удалось определить связанные политики:

| Настройка | Полученное значение |
|---|---|
| Marketplace | EBAY_DE |
| Обработка заказа | handlingTime: 7 DAY |
| Domestic service | DE_SonstigeDomestic, carrier GENERIC |
| Domestic стоимость | 0 EUR, freeShipping=true |
| Дополнительная единица domestic | 0 EUR |
| International service | DE_SonstigeInternational |
| International стоимость | 29.99 EUR |
| Дополнительная единица international | 29.99 EUR |
| Immediate payment | true |
| Возвраты | Принимаются |
| Срок возврата | 1 MONTH |
| Обратная доставка | SELLER |
| freightShipping | false |
| globalShipping | false |
Это условия одного конкретного предложения DEP, а не универсальные условия JV/XL/DEP и не тариф для всей мебели.

В международной области включены AT, CH, ES, FR, GB, IT, PL, а также группы Europe и EuropeanUnion.

Исключены группы Africa, Asia, Central America and Caribbean, Middle East, North America, Oceania, South America, Southeast Asia и регионы Hiddensee, Nordfriesische Inseln, Ostfriesische Inseln.

Источник — прочитанные политики, на которые ссылается DEP offer. Физические договорённости с перевозчиками этой проверкой не подтверждены.

### 5.4. Примеры запросов

Важно: исходные POST-запросы создания этих реальных политик не сохранились в найденных материалах. Ниже — реконструкция по текущим GET-ответам, без реальных ID, с новым условным названием. Это не запись исторически отправленного запроса.

#### Fulfillment: domestic-часть найденной конфигурации


POST /sell/account/v1/fulfillment_policy



{
  "name": "Example DE domestic shipping",
  "marketplaceId": "EBAY_DE",
  "categoryTypes": [
    {
      "name": "ALL_EXCLUDING_MOTORS_VEHICLES"
    }
  ],
  "handlingTime": {
    "value": 7,
    "unit": "DAY"
  },
  "shippingOptions": [
    {
      "optionType": "DOMESTIC",
      "costType": "FLAT_RATE",
      "shippingServices": [
        {
          "sortOrder": 1,
          "shippingCarrierCode": "GENERIC",
          "shippingServiceCode": "DE_SonstigeDomestic",
          "shippingCost": {
            "value": "0.00",
            "currency": "EUR"
          },
          "additionalShippingCost": {
            "value": "0.00",
            "currency": "EUR"
          },
          "freeShipping": true
        }
      ]
    }
  ]
}


Это только domestic-пример, без международной части и исключений реальной политики.

Службу нужно проверять для целевой площадки, а не копировать код USPS из примеров для США. Документация описывает получение допустимых shipping service codes. [Выбор перевозчика и сервиса](https://developer.ebay.com/api-docs/sell/static/seller-accounts/ht_shipping-setting-shipping-carrier-and-service-values.html).

#### Payment


POST /sell/account/v1/payment_policy



{
  "name": "Example DE payment",
  "marketplaceId": "EBAY_DE",
  "categoryTypes": [
    {
      "name": "ALL_EXCLUDING_MOTORS_VEHICLES"
    }
  ],
  "immediatePay": true
}


У прочитанной реальной политики paymentMethods — пустой массив. Это не означает отсутствие оплаты и не доказывает применимость такого запроса к любому ещё не подготовленному аккаунту.

#### Return


POST /sell/account/v1/return_policy



{
  "name": "Example DE returns",
  "marketplaceId": "EBAY_DE",
  "categoryTypes": [
    {
      "name": "ALL_EXCLUDING_MOTORS_VEHICLES"
    }
  ],
  "returnsAccepted": true,
  "returnPeriod": {
    "value": 1,
    "unit": "MONTH"
  },
  "returnShippingCostPayer": "SELLER"
}


Не заменять `1 MONTH` на `30 DAY` в описании нашего опыта: API вернул именно месяц.

### 5.5. Обработка заказа и доставка — разные сроки

- handlingTime — время продавца до передачи отправления перевозчику.
- Срок перевозки связан с выбранным shippingServiceCode и его метаданными.
- В Trading shipping metadata используются ShippingTimeMin / ShippingTimeMax; это не handling time.
- В прочитанной нашей политике отдельного произвольного срока перевозки не обнаружено.
- Наша обёртка shipping services не возвращает эти временные поля — только коды, описание, категорию, международность и cost types.

Источники: services/database-service/ebay_service/client.py:1130, [смысл handling time](https://developer.ebay.com/api-docs/sell/static/seller-accounts/ht_shipping-free.html), [время перевозки](https://developer.ebay.com/DevZone/XML/Docs/Reference/eBay/types/ShippingServiceOptionsType.html).

Для крупногабаритной мебели: у найденного примера freightShipping=false. Подтверждения конкретной Spedition, доставки до бордюра, двух грузчиков или сборки не найдено. Нельзя брать подобные тексты из OTTO и считать их нашими условиями eBay.
### 5.6. Адрес возврата

В проверенном нашем коде создания return_policy отдельный адрес возврата не задаётся.

Документация: eBay использует адрес возврата и настройки Return Preferences аккаунта; правила возврата могут задавать отдельный адрес, иначе используется адрес по умолчанию. [Настройки обработки возвратов](https://developer.ebay.com/api-docs/user-guides/static/post-order-user-guide/post-order-returns.html).

Следовательно:

- владелец должен отдельно проверить адрес возврата в аккаунте;
- merchantLocationKey нельзя считать настройкой адреса возврата;
- наш код не требует совпадения адреса возврата и склада;
- фактическое совпадение адресов наших аккаунтов не проверено.

### 5.7. Общие настройки и настройки offer

Код: политики существуют у аккаунта, а каждый offer ссылается на выбранные ID:


{
  "listingPolicies": {
    "fulfillmentPolicyId": "<этого продавца>",
    "paymentPolicyId": "<этого продавца>",
    "returnPolicyId": "<этого продавца>"
  }
}


Для другого товара можно выбрать другую политику. Цена, количество, категория и склад задаются на уровне соответствующего товара/offer.

Рекомендация: не редактировать общую политику ради одного товара без проверки остальных использующих её объявлений; создать отдельную политику и назначить её этому offer.

---

## 6. Склад и местонахождение товара

### Наш запрос создания

Код:


POST /sell/inventory/v1/location/{merchantLocationKey}


Обезличенный пример точной структуры, которую формирует наш клиент:


{
  "name": "Example warehouse",
  "location": {
    "address": {
      "postalCode": "<реальный индекс>",
      "country": "DE"
    }
  },
  "locationTypes": [
    "WAREHOUSE"
  ],
  "merchantLocationStatus": "ENABLED"
}


Источник: services/database-service/ebay_service/client.py:275.

Это не исторический лог создания конкретного склада.

### Обязательные адресные поля

Для нашего `WAREHOUSE`-сценария локальная проверка допускает:


country + postalCode


либо:


country + city + stateOrProvince


Улица поддерживается, но нашей проверкой не требуется. Ключ склада проверяется на непустоту и длину до 50 символов.

Источник: services/database-service/ebay_service/views.py:808.

Документация eBay для warehouse описывает тот же минимальный адрес; для store/fulfillment center требования отличаются. [Inventory locations](https://developer.ebay.com/api-docs/sell/static/inventory/managing-inventory-locations.html).

Рекомендация для Production: владелец предоставляет полный реальный адрес, даже если минимальный API-запрос принимает только страну и индекс.

### Как выбираем склад

Код:

- JV выбирает merchantLocationKey в форме.
- Для XL/DEP при отсутствии выбора предусмотрен default-склад: страна DE, индекс, зашитый в проекте.
- Клиент ищет подходящий включённый warehouse; при неоднозначности останавливается.
- Если подходящего склада нет, код может создать его.

Источник: services/database-service/ebay_service/listing_operations.py:18.

Это наша специфическая бизнес-настройка. Коллеге нельзя копировать её индекс или автоматически создавать «немецкий склад», если товар реально отправляется из другой страны.

Связь с предложением:


{
  "merchantLocationKey": "example-warehouse-de"
}


### Один или несколько складов

- Код и API: один ключ можно использовать в нескольких offers.
- Prod-проверка: JV имеет несколько locations; XL и DEP — по одному.
- Наш обычный offer содержит один merchantLocationKey.
- Полноценное распределение одного предложения между несколькими складами в этом сценарии не подтверждено.

### Разные страны продавца, товара и отправления

Нужно разделять:

1. страну регистрации продавца;
2. площадку размещения EBAY_DE;
3. фактическое местонахождение/отправление товара;
4. страны назначения.

Они не должны автоматически подменять друг друга.
Prod-проверка: в реестре JV есть warehouse locations с кодами BE, BG, DE, ES, FR, GB, IT, NL, PL. Но это записи eBay, не доказательство физического наличия товара во всех этих странах.

В нашем коде нет автоматического пересчёта сроков или стоимости доставки по стране склада. Владелец должен выбрать корректный склад и совместимую политику; eBay дополнительно применяет свои ограничения и расчёты.

---

## 7. Что владелец обязан предоставить вручную

Перед Production нужно согласовать:

| Данные | Что предоставить |
|---|---|
| Продавец | Реальный аккаунт и завершённые требования регистрации |
| Склад | Фактический адрес отправления и название |
| Доставка | Страны, исключённые регионы, перевозчики, сервисы |
| Стоимость | Первая единица и каждая дополнительная |
| Handling time | Реальное время подготовки заказа |
| Мебель | Условия Spedition, разгрузки, сборки — если действительно предлагаются |
| Возвраты | Срок, плательщик обратной доставки, реальный адрес |
| Оплата | Настройки, соответствующие аккаунту |
| Товар | Реальные характеристики, состояние, остаток, GTIN, фотографии |
| Приложение | Домены callback, privacy policy, обработка уведомлений |

Sandbox: можно использовать явно тестовые названия, тестовый товар и согласованные тестовые тарифы, не выдавая их за Production-условия.

Production: адреса, наличие, сроки и обещанные услуги должны соответствовать реальной работе продавца. Наш пример бесплатной доставки или возврата за счёт продавца не является универсальной рекомендацией.

---

## 8. Проверка готовности перед публикацией

Рекомендуемый контрольный набор:

| Запрос | Достаточный результат |
|---|---|
| GET /sell/account/v1/privilege | sellerRegistrationCompleted=true; лимиты позволяют размещение |
| GET /sell/account/v1/program/get_opted_in_programs | Есть SELLING_POLICY_MANAGEMENT |
| GET /sell/account/v1/fulfillment_policy?marketplace_id=EBAY_DE | Есть подходящий fulfillment policy ID |
| GET /sell/account/v1/payment_policy?marketplace_id=EBAY_DE | Есть payment policy ID |
| GET /sell/account/v1/return_policy?marketplace_id=EBAY_DE | Есть return policy ID |
| GET /sell/inventory/v1/location | Есть выбранный включённый warehouse |
| Taxonomy category/aspects | Конечная категория, заполнены обязательные характеристики |

### Что проверяет именно наш код

Наш seller_setup запрашивает locations и три вида политик. `getPrivileges` в него не входит, локального запрета по sellerRegistrationCompleted=false в проверенном publish path нет.

Источники: services/database-service/ebay_service/client.py:244, services/database-service/ebay_service/listing_operations.py:562.

Однако отсутствие проверки в коде не означает, что eBay разрешает публикацию незарегистрированному продавцу.

На вопросы:

- «Был ли true обязательным условием нашего кода?» — нет отдельной проверки этого флага.
- «Удалось ли нам публиковать при false?» — я не могу это подтвердить.
- «Есть ли доказательство, что false можно игнорировать?» — нет.

Для коллеги правильный порядок — сначала разобраться с регистрацией Sandbox seller, а не пытаться обойти флаг.

---

## 9. Первая публикация

После подготовки аккаунта:


Seller готов
→ Business Policies
→ warehouse
→ категория и атрибуты
→ inventory item
→ offer
→ publish
→ проверка результата


Точные внешние операции:


PUT  /sell/inventory/v1/inventory_item/{sku}
POST /sell/inventory/v1/offer
POST /sell/inventory/v1/offer/{offerId}/publish
GET  /sell/inventory/v1/offer/{offerId}
GET  /sell/inventory/v1/inventory_item/{sku}


В нашем Create Product используется:
{
  "sku": "<уникальный SKU>",
  "marketplaceId": "EBAY_DE",
  "format": "FIXED_PRICE",
  "categoryId": "<leaf category>",
  "merchantLocationKey": "<warehouse key>",
  "availableQuantity": 1,
  "listingDuration": "GTC",
  "pricingSummary": {
    "price": {
      "value": "<цена>",
      "currency": "EUR"
    }
  },
  "listingPolicies": {
    "fulfillmentPolicyId": "<ID>",
    "paymentPolicyId": "<ID>",
    "returnPolicyId": "<ID>"
  }
}


Источник: apps/frontend/app/create-product/use-create-product-controller.ts:1191.

Код: перед публикацией проверяет обязательные поля, категорию/атрибуты и фотографии; затем создаёт или использует существующий offer и сохраняет listingId.

Источник: services/database-service/ebay_service/listing_operations.py:385.

Получить offerId недостаточно: требуется успешный publish. Проверять нужно также фактическое состояние объявления, а не только статус внутренней очереди. [Поля, необходимые для публикации](https://developer.ebay.com/api-docs/sell/static/inventory/publishing-offers.html).

Наш критерий приёмки — Item ID / Offer ID, правильный продавец и доступность карточки; runbook требует отдельной внешней проверки. Сам runbook не доказывает, что вся матрица выполнена.

Источник: docs/runbooks/ebay-acceptance.md:15.

---

## 10. Переход из Sandbox в Production

Повторить или заменить:

- Production Client ID / Secret.
- Production RuName и настройки redirect.
- Production OAuth consent реального продавца.
- Production refresh token.
- Проверку регистрации продавца и выплат.
- Подключение Business Policies.
- Создание/выбор Production policies.
- Создание/выбор реальных inventory locations.
- Проверку категории и требований целевой площадки.
- Проверку notifications/keyset readiness.

Не переносить как готовые Production-ресурсы:

- Sandbox access/refresh tokens;
- Sandbox policy IDs;
- Sandbox offer IDs и listing IDs;
- тестовые адреса и обещания доставки.

Ключ склада можно назвать так же, но ресурс должен существовать в выбранном окружении и аккаунте. Одинаковое имя не создаёт общей сущности.

---

## 11. Ошибки из нашего опыта и границы доказательств

| Ситуация | Что подтверждено |
|---|---|
| 404 / 25713, offer отсутствует | Код теперь трактует именно такой ответ поиска как отсутствие offer; другие ошибки не скрывает |
| Слишком длинное описание | В переписке был отказ 25718; в форме есть лимит inventory description 4000 |
| Маленькое изображение | В переписке был отказ при publish; код Inventory проверяет минимум 500 px по длинной стороне |
| Неверно выбранный OAuth-аккаунт | Код использует prompt=login; это не заменяет проверку владельца подключения |
| Неоднозначный warehouse | Код останавливает выбор вместо угадывания |
| Отсутствие сохранённой привязки offer | В коде предусмотрены поиск и восстановление; неоднозначность блокирует операцию |
| sellerRegistrationCompleted=false | Наше успешное исправление и публикация при false не установлены |
| 401 Invalid access token | Есть refresh flow, но отдельный автоматический retry после 401 не найден |

Источники: services/database-service/ebay_service/client.py:385, apps/frontend/app/create-product/create-product-model.ts:25, services/database-service/ebay_service/image_validation.py:15.

Итог для коллеги: сейчас следующий шаг — валидный Sandbox seller с завершённой регистрацией, затем три политики EBAY_DE, включённый warehouse и только после этого первая публикация. Production-настройки нашего проекта не являются доказательством, что их Sandbox можно использовать с sellerRegistrationCompleted=false.

Ничего не изменял и новые объявления не публиковал. Исторические ручные действия, Sandbox-публикация и текущая покупаемость ранее созданного DEP listing не подтверждены; выполнена только проверка чтением Production API.
