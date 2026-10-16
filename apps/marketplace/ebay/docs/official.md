# eBay: официальная документация и выводы для BENIM

Дата изучения: **16 октября 2026 года**. Целевой сценарий: **JV → EBAY_DE → EUR → фиксированная цена**, затем отдельные подключения XL и Depotum.

## 1. Источники и границы проверки

Отправные страницы:

- [Руководства Sell](https://developer.ebay.com/develop/guides/sell/).
- [Каталог API](https://developer.ebay.com/develop/api).

Ниже разобраны 16 тематических разделов меню Sell: назначение, основные сценарии, ограничения и применимость к нашему проекту. Дополнительно проверены материалы по регистрации, политикам, складам, публикации, ограничениям и спецификации ключевых API.

**Ограничение доступа:** корневая страница Sell не открылась напрямую, а каталог `/develop/api` и часть страниц справочника превышали лимит размера веб-инструмента. Разделы изучались по отдельным официальным страницам, доступным из меню руководства; каталог сопоставлен с этими руководствами, обзорами API, таблицей лимитов и OpenAPI. Браузерный способ открытия также оказался недоступен. Полную проверку всех карточек и каждого метода всего каталога выполнить не удалось; это не построчный аудит всей документации eBay.

Различаем три вида сведений:

- **Документация eBay** — требования и возможности, описанные официальными источниками.
- **Факт нашей настройки** — ответы API, которые владелец BENIM прислал в переписке. Это не новая проверка сервера 16 октября.
- **Вывод для BENIM** — предлагаемый порядок реализации; он сам по себе не означает, что функция уже реализована.

Материал коллеги [ebay3.md](ebay3.md) относится к **WareHub**. Его Production-аккаунты, URL обёрток, настройки и результаты не являются проверкой BENIM.

## 2. Что делать нам сейчас

**Продолжать подготовку политик и склада, параллельно решая регистрацию тестового продавца.** Ни OAuth, ни Business Policies не завершают регистрацию продавца автоматически.

Последний присланный результат JV в Sandbox:

| Проверка | Результат |
| --- | --- |
| Запросы readiness | Выполнены успешно, `errors = {}` |
| Авторизация | Позволяет читать настройки продавца |
| `SELLING_POLICY_MANAGEMENT` | Включена |
| `sellerRegistrationCompleted` | `false`, также подтверждено в API Explorer |
| Три типа политик для `EBAY_DE` | Пустые списки |
| Склады | Пустой список |
| Лимит продавца | Не возвращён |

Следующие этапы:

1. **Регистрация Sandbox.** Контрольный вариант — новый тестовый пользователь через официальный инструмент создания пользователей, с подходящим registration site, затем проверка в API Explorer. Имя с суффиксом `seller` само по себе не активирует продажи. Существующее подключение JV не заменять вслепую: сначала проверить нового пользователя отдельно. Если статус снова `false`, передать воспроизводимый пример в Developer Technical Support.
2. **Условия продавца.** Согласовать страны доставки, услуги, стоимость первой и дополнительной единицы, handling time, возвраты и фактическое местонахождение товара.
3. **Подготовка через BENIM.** Реализовать чтение и выбор существующих политик и складов, затем явное создание недостающих ресурсов. Это следующий предлагаемый этап, а не уже существующие эндпоинты нашего проекта.
4. **Категории.** Получить категорию и её требования для `EBAY_DE`, подготовить проверяемый товар.
5. **Публикация.** После разрешения вопросов аккаунта пройти `inventory item → offer → publish → повторный GET`. Получение `offerId` ещё не подтверждает размещение.

Для Production, если владелец подключит те же реальные аккаунты, что использует WareHub, сначала прочитать существующие ресурсы и выбрать подходящие. Новое приложение не означает необходимость заново создавать все политики и склады продавца. Их наличие у нужного аккаунта и в нужном окружении требуется проверить отдельно.

Основание: [создание пользователя Sandbox](https://developer.ebay.com/develop/guides/sell/get-started-with-ebay-apis), [статус продавца](https://developer.ebay.com/api-docs/sell/static/seller-accounts/ht_get-selling-limits.html), [Account API](https://developer.ebay.com/api-docs/sell/account/static/overview.html).

## 3. Краткое содержание разделов Sell

### 3.1. Get started with eBay APIs

Описывает developer account, ключи, рынки, тестовых пользователей, Sandbox/Production, API Explorer, SDK/OpenAPI, поддержку и защиту приложения. Developer account, реальный продавец и Sandbox user — разные учётные записи. Ключи и пользовательские токены должны соответствовать окружению.

**Для BENIM:** один набор ключей приложения не заменяет отдельный OAuth consent каждого продавца. Sandbox предназначен для функциональных сценариев, не нагрузочной проверки eBay.

[Раздел](https://developer.ebay.com/develop/guides/sell/get-started-with-ebay-apis).

### 3.2. Using eBay RESTful APIs

Разбирает запросы, заголовки, ответы, ошибки, предупреждения, версии и поддержку. Требования к marketplace и языку зависят от метода: нельзя рассчитывать, что один заголовок заменяет параметр в URL или теле. В ошибках полезны `errorId`, `message`, `longMessage`, ссылки на поля и параметры.

**Для BENIM:** различать ошибку авторизации, валидацию, бизнес-отказ и временный сбой; не повторять все операции одинаково. Успешный HTTP-код не отменяет необходимости разобрать результат операции.

[Раздел](https://developer.ebay.com/develop/guides/sell/using-ebay-restful-apis).

### 3.3. Using eBay GraphQL APIs

Рассматривает схему, операции, переменные, заголовки и ошибки GraphQL. Ответ `200` может содержать `errors`; параметры передаются через variables. Для соответствующих операций используются собственные правила лимитов.

**Для BENIM:** GraphQL не требуется для базовой цепочки Inventory API. Не добавлять его только потому, что он присутствует в документации; он понадобится при выборе конкретной функции, например Inventory Mapping.

[Раздел](https://developer.ebay.com/develop/guides/sell/using-ebay-graphql-apis).

### 3.4. Authorization

Описывает scopes, Application и User tokens, client credentials, authorization code, refresh, introspection/revocation и совместимость с Traditional API. Для операций продавца нужен пользовательский доступ после согласия. В OAuth `redirect_uri` используется RuName. Сроки токенов берутся из ответа сервиса.

**Для BENIM:** сохранять подключения отдельно по аккаунту и окружению. После отзыва refresh token нужен новый consent. Introspection не является регистрацией продавца. Доступ к Identity требует соответствующего scope.

[Раздел](https://developer.ebay.com/develop/guides/sell/authorization).

### 3.5. Listing Creation

Сравнивает Inventory, Trading и Sell Feed; рассматривает обычные объявления, вариации, каталог и массовую загрузку. В Inventory данные товара и условия продажи разделены. Для вариаций дополнительно используются группы inventory items.

**Для BENIM:** начать с одного товара и фиксированной цены через Inventory API. Массовую обработку и вариации добавлять после завершения первого сценария. Новый SKU не заменяет настоящий EAN.

[Раздел](https://developer.ebay.com/develop/guides/sell/listing-creation).

### 3.6. Listing Management

Описывает чтение, изменение, повторное размещение и завершение объявлений через Inventory, Trading и Feed. Replace-операции требуют сохранения применимых полей; изменение только одного поля не делает PUT частичным PATCH.

**Для BENIM:** хранить связь товара с SKU, offerId и listingId; перед изменением читать текущие данные. Снятие с продажи и удаление — разные действия. После неопределённого результата сначала искать уже созданный ресурс.

[Раздел](https://developer.ebay.com/develop/guides/sell/listing-management).

### 3.7. Listing Metadata

Описывает дерево категорий, конечные категории, аспекты, состояния, правила возврата и особенности рынка. Taxonomy помогает выбрать категорию и характеристики; Metadata задаёт допустимые возможности и ограничения. Коды перевозчиков и услуг проверяются для нужной площадки через современные методы Metadata. Упоминания Legacy-методов в руководстве сверяются с разделом 10 ниже.

**Для BENIM:** не переносить категорию OTTO или американские примеры в eBay Germany. Обязательные характеристики проверять по категории; подсказки ИИ не заменяют эту проверку.

[Раздел](https://developer.ebay.com/develop/guides/sell/listing-metadata-guide).

### 3.8. Account Management

Объединяет политики, custom policies, настройки аккаунта, программы, тарифные таблицы, финансы и правила совместной доставки. Политики существуют у продавца, а предложения ссылаются на их ID. Есть функции с региональными ограничениями.

**Для BENIM:** сначала читать существующие настройки; общую политику не менять ради одного товара без понимания последствий для остальных объявлений. Обзор не заменяет актуальные примечания конкретного метода — см. раздел 6 ниже.

[Раздел](https://developer.ebay.com/develop/guides/sell/account-management-guide).

### 3.9. Analytics and Reporting

Описывает качество продавца, сервисные метрики, показы, переходы и конверсии. Доступность данных зависит от поддерживаемой площадки, периода и истории аккаунта.

**Для BENIM:** полезно после публикаций и продаж. Sell Analytics — аналитика торговли; её нельзя путать с Developer Analytics, которая показывает использование API.

[Раздел](https://developer.ebay.com/develop/guides/sell/analytics-and-reporting-guide).

### 3.10. Communications

Покрывает Notification API, темы и подписки, проверку уведомлений, предложения заинтересованным покупателям, сообщения и отзывы. У подписок есть требования к endpoint, scope и проверке подлинности. Для сообщений и отзывов существуют отдельные сценарии.

**Для BENIM:** уведомления об удалении аккаунта — обязательство приложения, а сообщения покупателям и маркетинговые предложения — отдельные будущие функции. Получение webhook не должно автоматически запускать непроверенное действие.

[Раздел](https://developer.ebay.com/develop/guides/sell/sell-communications-guide).

### 3.11. Order Management

Рассматривает заказы, отправления, tracking, отмены, недоставленные товары, возвраты и платёжные споры. Fulfillment API и Post-Order API решают разные части процесса, но часть перечисленных в руководстве Post-Order операций уже отключена: см. раздел 10. У отдельных Logistics-сценариев есть ограничения по региону и доступу.

**Для BENIM:** сначала отдельный импорт заказов и tracking. Политика возврата при размещении не равна обработке фактического возврата после продажи. Перед финансовыми действиями проверять требования к подписи.

[Раздел](https://developer.ebay.com/develop/guides/sell/order-management-guide).

### 3.12. Marketing and Promotions

Описывает рекламные кампании, модели оплаты рекламы, отчёты, скидки, купоны и email-кампании магазина. Правила допуска и набор доступных функций различаются.

**Для BENIM:** создание объявления не должно автоматически запускать платную рекламу. Это отдельная функция с собственными условиями, бюджетом и разрешениями.

[Раздел](https://developer.ebay.com/develop/guides/sell/marketing-and-promotions-guide).

### 3.13. Other APIs

Объединяет перевод, проверку нарушений, Best Offers, Second Chance Offers, leads, VeRO, Identity и региональную логистику eDIS. Эти сервисы имеют разные задачи и ограничения.

**Для BENIM:** Identity нужен для привязки продавца. Первоначальная рекомендация рассмотреть Compliance отозвана: этот API уже отключён. Для VeRO и leads также нужны актуальные замены из раздела 10. API-предложение `offer` из Inventory нельзя путать со ставкой или Best Offer покупателя. Наличие Translation API не гарантирует любую языковую пару.

[Раздел](https://developer.ebay.com/develop/guides/sell/other-apis-guide).

### 3.14. Digital Signatures for APIs

Для определённых финансовых операций продавцов ЕС/Великобритании нужны дополнительные подписи HTTP-запросов. Раздел описывает область применения, ключи, digest, signature headers, SDK и ошибки проверки.

**Для BENIM:** это не универсальное требование ко всем вызовам eBay. Для будущих финансов и возвратов денег нужно отдельно проверить конкретный метод; подпись уведомления eBay и подпись нашего исходящего запроса — разные механизмы.

[Раздел](https://developer.ebay.com/develop/guides/sell/digital-signatures-for-apis).

### 3.15. Regulated Third Party Providers — TPP

Это специальная интеграция регулируемых платёжных провайдеров: регистрация клиента, сертификаты, consent, особые gateway и финансовые методы.

**Для BENIM:** обычная интеграция продавца для CRUD товаров не означает статус регулируемого TPP. Не переносить из этого раздела регистрацию, сертификаты и адреса `tppz` в наш основной OAuth-клиент.

[Раздел](https://developer.ebay.com/develop/guides/sell/regulated-third-party-providers-tpp).

### 3.16. Marketplace User Account Deletion

Описывает подписку или допустимое освобождение, HTTPS endpoint, challenge, уведомление, подтверждение получения, проверку подписи и обработку данных пользователя. Возврат `204` сам по себе не доказывает выполнение обработки.

**Для BENIM:** этот endpoint принимает событие об удалении, произошедшем на eBay; он не является командой удаления самого eBay-аккаунта. В этой работе endpoint и тестовые уведомления не вызывались.

[Раздел](https://developer.ebay.com/develop/guides/sell/marketplace-user-account-deletion).

## 4. Карта каталога API для нашей задачи

Каталог группирует современные и Traditional API по сценариям. Следующая таблица — карта назначения, восстановленная по доступным официальным разделам, а не заявление о проверке всех методов каталога.

| Группа | Основные API / возможности | Приоритет BENIM |
| --- | --- | --- |
| Listing Management | Inventory; Feed для массовых заданий; Media; Inventory Mapping; Traditional listing calls | Inventory сейчас; остальные по потребности |
| Listing Metadata | Taxonomy, Metadata, Catalog, Charity, Traditional metadata calls | Taxonomy и Metadata сейчас |
| Account Management | Account v1/v2, Finances, Traditional user account calls | Account v1 сейчас |
| Order Management | Fulfillment, Post-Order, Logistics, Traditional order calls | Следующий самостоятельный этап |
| Offers and Leads | Negotiation, Best Offers, Second Chance Offers, leads | После базового CRUD |
| Communication | Notification, Message, Feedback, Traditional communications | Удаление данных сейчас; остальное позже |
| Marketing and Promotions | Sell Marketing, Recommendation | Отдельное решение владельца |
| Analytics and Reporting | Sell Analytics | После появления объявлений и продаж |
| Other APIs | Identity, Translation, VeRO v2, eDIS; Compliance только как устаревшее упоминание | Identity сейчас; остальные актуальные API по задаче |
| App Settings & Insight | Developer Analytics, Key Management, Client Registration; новые средства мониторинга приложения | Наблюдение за квотами; подписи/TPP только при необходимости |
| Buy: discovery / metadata / discounts | Browse, Buy Feed, Deal, Buy Marketing, общие metadata-сервисы | Не нужны для выставления наших товаров |
| Buy: checkout / bids / communications | Order, Offer, buyer communications | Другой сценарий: покупка товаров, не размещение продавцом |

В Buy-группе часть API требует специального доступа. Разрешение на Sell Inventory не означает допуск ко всем Buy-сервисам. Источники: [Buying Integration Guide](https://developer.ebay.com/api-docs/buy/buying-ig-landing.html), [Buy API requirements](https://developer.ebay.com/api-docs/buy/buy-requirements.html).

Группы Sell и ограничения сопоставлены с [официальной таблицей API Call Limits](https://developer.ebay.com/develop/api/sell/api_call_limits), разделами 3.5–3.15 и [Release Notes](https://developer.ebay.com/develop/api/sell/release_notes). Версии и наличие API не означают автоматический доступ нашего приложения.

## 5. Минимальная последовательность внешних запросов

Это **маршруты eBay**, не обещание существования одноимённых endpoint в BENIM. Для Sandbox применяется `https://api.sandbox.ebay.com`, для Production — `https://api.ebay.com`, если справочник конкретного API не задаёт другой host.

### Проверка и подготовка аккаунта

```http
GET /sell/account/v1/privilege
GET /sell/account/v1/program/get_opted_in_programs
GET /sell/account/v1/fulfillment_policy?marketplace_id=EBAY_DE
GET /sell/account/v1/payment_policy?marketplace_id=EBAY_DE
GET /sell/account/v1/return_policy?marketplace_id=EBAY_DE
GET /sell/inventory/v1/location
```

При необходимости и после согласования условий:

```http
POST /sell/account/v1/program/opt_in
POST /sell/account/v1/fulfillment_policy
POST /sell/account/v1/payment_policy
POST /sell/account/v1/return_policy
POST /sell/inventory/v1/location/{merchantLocationKey}
```

Основание: [Account API overview](https://developer.ebay.com/api-docs/sell/account/static/overview.html), [Inventory locations](https://developer.ebay.com/api-docs/sell/static/inventory/managing-inventory-locations.html).

### Категория, товар и предложение

```http
GET /commerce/taxonomy/v1/get_default_category_tree_id?marketplace_id=EBAY_DE
GET /commerce/taxonomy/v1/category_tree/{treeId}/get_category_suggestions?q={text}
GET /commerce/taxonomy/v1/category_tree/{treeId}/get_item_aspects_for_category?category_id={categoryId}

PUT /sell/inventory/v1/inventory_item/{sku}
POST /sell/inventory/v1/offer
POST /sell/inventory/v1/offer/{offerId}/publish
GET /sell/inventory/v1/offer/{offerId}
```

Необходимы подходящая конечная категория, характеристики, состояние, изображения, цена, остаток, политики и местонахождение. Дополнительные требования определяются категорией и текущей схемой. Сохранённый черновик может пройти создание, но не пройти публикацию.

Источники: [Taxonomy](https://developer.ebay.com/api-docs/commerce/taxonomy/static/overview.html), [обязательные поля publish](https://developer.ebay.com/api-docs/sell/static/inventory/publishing-offers.html).

### Изменение и снятие

```http
GET /sell/inventory/v1/inventory_item/{sku}
GET /sell/inventory/v1/offer/{offerId}
PUT /sell/inventory/v1/inventory_item/{sku}
PUT /sell/inventory/v1/offer/{offerId}
POST /sell/inventory/v1/bulk_update_price_quantity
POST /sell/inventory/v1/offer/{offerId}/withdraw
```

Удаление offer или inventory item должно быть отдельным сценарием с ясными последствиями. Для существующих Legacy-объявлений сначала проверить возможность миграции или необходимость Trading API, а не создавать дубликат.

Источник: [Inventory API overview](https://developer.ebay.com/api-docs/sell/inventory/overview.html), [Inventory OpenAPI](https://developer.ebay.com/api-docs/master/sell/inventory/openapi/3/sell_inventory_v1_oas3.json).

## 6. Важные уточнения и найденные расхождения

### Регистрация и устаревшие рекомендации

- `sellerRegistrationCompleted=false` означает незавершённую регистрацию. Положительный ответ OAuth и включённая программа не меняют смысл этого поля.
- `ValidateTestUserRegistration` отключён **30 января 2025 года**. Совет из старой Knowledge Base нельзя применять. [Release Notes 1399](https://developer.ebay.com/devzone/xml/docs/releasenotes.html).
- Обзор Account ещё упоминает onboarding и KYC как диагностические возможности. Однако актуальные примечания OpenAPI помечают `getPaymentsProgramOnboarding` как неприменимый, а для `getKYC` ожидают `204`. Не считать их подтверждённым способом активации Sandbox. [Account OpenAPI](https://developer.ebay.com/api-docs/master/sell/account/openapi/3/sell_account_v1_oas3.json).
- Документированного актуального вызова, который гарантированно устанавливает нашему тестовому продавцу `true`, при этом изучении не найдено. Отсутствие найденного способа не доказывает отсутствие внутренних процедур eBay.

### Лимиты приложения и продавца

API Call Limits ограничивают запросы приложения; `sellingLimit` относится к торговым ограничениям продавца. В схеме `SellingLimit` описан месячный предел, хотя обзор и описание операции местами говорят о дневном. Нельзя превращать этот ответ в точный оставшийся лимит активных карточек.

`selling_limit=null` в нашем отчёте не означает нулевой лимит или доказанную безлимитность.

Источники: [Account OpenAPI, SellingLimit](https://developer.ebay.com/api-docs/master/sell/account/openapi/3/sell_account_v1_oas3.json), [API Call Limits](https://developer.ebay.com/develop/api/sell/api_call_limits).

### Проверка политик в текущем BENIM

При чтении `../readiness.py` было обнаружено сравнение с `ALL_EXCLUDING_MOTORS`. Для обычных товаров требуется **`ALL_EXCLUDING_MOTORS_VEHICLES`**. Это отдельная ошибка локальной проверки: правильная политика могла остаться отмеченной как неподходящая. Пока списки пусты, эта строка не объясняет незавершённую регистрацию.

**Обновление при реализации справочника:** сравнение исправлено на `ALL_EXCLUDING_MOTORS_VEHICLES`. Новые маршруты категорий и характеристик описаны в [ebay-setup.md](ebay-setup.md). Это не исправление регистрации продавца.

Основание: [Account OpenAPI, CategoryType и ReturnPolicyRequest](https://developer.ebay.com/api-docs/master/sell/account/openapi/3/sell_account_v1_oas3.json), пример в [ebay3.md](ebay3.md), непосредственное чтение `../readiness.py`.

### Identity и окружение

Для Identity используется `apiz`, а не универсальный `api` host; параметры и разрешения надо брать из его спецификации. В Sandbox Identity возвращает тестовые данные — их нельзя считать доказательством личности реального Production-продавца.

Источник: [Identity OpenAPI](https://developer.ebay.com/api-docs/master/commerce/identity/openapi/3/commerce_identity_v1_oas3.json).

## 7. Доставка, склад, возврат: что согласовать

| Настройка | Что нужно решить |
| --- | --- |
| Местонахождение | Фактическая страна и адрес отправления; склад выбирается отдельно от страны продавца и рынка `EBAY_DE` |
| `merchantLocationKey` | Уникальный ключ склада продавца, до 50 символов; не идентификатор адреса возврата |
| Адрес warehouse | Минимум `country + postalCode` либо `country + city + stateOrProvince`; полный адрес для реальной работы уточняет владелец |
| Handling time | Время подготовки и передачи перевозчику, не весь срок получения товара |
| Перевозка | Подходящий shipping service для рынка, география и ограничения; нельзя копировать американский сервис из примера |
| Стоимость | Первая и дополнительная единица, domestic/international; бесплатность не назначать без решения владельца |
| Возвраты | Принятие, период, плательщик обратной доставки и настройки адреса возврата аккаунта |

Минимум адреса и требования разных типов location описаны в [управлении складами](https://developer.ebay.com/api-docs/sell/static/inventory/managing-inventory-locations.html). Для срока обработки и стоимости сверяться с [Account API](https://developer.ebay.com/api-docs/sell/account/static/overview.html), для кодов услуг — с [Listing Metadata](https://developer.ebay.com/develop/guides/sell/listing-metadata-guide).

Условия DEP из `ebay3.md` не назначаются JV автоматически. Не выдавать `1 MONTH` за исторически полученное `30 DAY`; не объявлять обычную доставку услугой Spedition, разгрузкой или сборкой без подтверждения.

Адрес возврата проверяется отдельно в настройках возвратов. Источник: [Post-order returns](https://developer.ebay.com/api-docs/user-guides/static/post-order-user-guide/post-order-returns.html).

## 8. Переход в Production

1. Проверить готовность Production keyset и обработку уведомлений приложения.
2. Использовать Production RuName и OAuth реального продавца.
3. Прочитать его регистрацию, ограничения, программы, политики и locations.
4. Выбрать существующие корректные ресурсы либо согласовать создание новых.
5. Проверить данные конкретного товара и требования категории.
6. Выполнять реальную публикацию отдельным согласованным действием; затем сохранить и проверить результат.

Sandbox-токены и ID ресурсов не переносятся в Production. Даже одинаковое имя склада должно соответствовать отдельному ресурсу нужного аккаунта и окружения. Регистрацию, завершённую в реальном аккаунте, нельзя использовать как доказательство активации тестового.

## 9. API Status: дополнение от 16 октября 2026 года

### 9.1. Что прочитано и как трактовать статусы

Проверены все три доступные страницы списка: [общая](https://developer.ebay.com/develop/get-started/api-status), [Production](https://developer.ebay.com/develop/get-started/api-status/production), [Sandbox](https://developer.ebay.com/develop/get-started/api-status/sandbox). Прочитаны доступные карточки всех 24 записей, найденных в объединении этих списков и более свежих страниц Support. Недоступные старые адреса проверялись через соответствующие опубликованные карточки Support/Resolved. Две отдельные записи GetItem оставлены раздельно, как на сайте.

В доступной текстовой выдаче этих списков ссылок на следующие страницы не показано. Проверка охватывает видимые записи и их карточки, но не гарантирует полный исторический архив eBay.

**Обнаружено расхождение версий страниц:** адреса `/develop/get-started/api-status/...` показывают старые состояния; [Production в Support](https://developer.ebay.com/support/api-status/production) и [Sandbox в Support](https://developer.ebay.com/support/api-status/sandbox) получены в более свежей выдаче. В таблице ниже приоритет дан обновлённой карточке события. Поле Last Update не всегда меняется вместе со статусом; это не доказанная дата начала или устранения сбоя.

`Resolved` означает, что eBay пометил конкретный инцидент исправленным. Это не гарантия исправности всех вызовов. `Unresolved` не означает, что у всех продавцов ломается весь API. Объявления об инфраструктурных изменениях также могут находиться в этом списке.

### 9.2. Все найденные записи

Даты — календарные даты Last Update, указанные eBay, без пересчёта их времени в часовой пояс BENIM.

| Дата | Событие и источник | Состояние прочитанной карточки |
| --- | --- | --- |
| 2026-10-15 | [Negotiation: sendOfferToInterestedBuyers, HTTP 500](https://developer.ebay.com/support/api-status/production/sendoffertointerestedbuyers-negotiation-api-returning-500-internal-error) | Unresolved |
| 2026-10-06 | [Inventory: publishOffer, неверный Lot Size](https://developer.ebay.com/support/api-status/production/resolved-publishoffer-call-returning-error-lot-size-is-invalid-need-to-be-greater-than-1-and-less-than-450000) | Resolved; старая карточка была Unresolved |
| 2026-09-21 | [Trading: AddFixedPriceItem / ReviseFixedPriceItem, System Error](https://developer.ebay.com/support/api-status/production/resolved-trading-api-addfixedpriceitem-and-revisefixedpriceitem-requests-failing-with-system-error-1) | Resolved; старая карточка была Unresolved |
| 2026-09-16 | [Developer Analytics: getRateLimits возвращал пустой 204](https://developer.ebay.com/support/api-status/production/resolved-getratelimits-api-returns-http-204-no-content) | Resolved |
| 2026-09-14 | [Не сохранялись параметры Account Deletion endpoint](https://developer.ebay.com/develop/get-started/api-status/production/resolved-error-while-saving-marketplace-account-deletion-endpoint-details) | Resolved |
| 2026-09-10 | [GetItem: отсутствовал ItemCompatibilityList, запись 1](https://developer.ebay.com/develop/get-started/api-status/production/resolved-trading-api-getitem-does-not-return-less-than-itemcompatibilitylist-greater-than-1) | Resolved |
| 2026-09-04 | [GetItem: отсутствовал ItemCompatibilityList, запись 2](https://developer.ebay.com/develop/get-started/api-status/production/resolved-trading-api-getitem-does-not-return-less-than-itemcompatibilitylist-greater-than) | Resolved |
| 2026-08-27 | [Trading: прежний инцидент AddFixedPriceItem / ReviseFixedPriceItem](https://developer.ebay.com/develop/get-started/api-status/production/resolved-trading-api-addfixedpriceitem-and-revisefixedpriceitem-requests-failing-with-system-error) | Resolved |
| 2026-07-27 | [Marketing: getCampaigns не возвращал рекламу из Seller Hub](https://developer.ebay.com/develop/get-started/api-status/production/resolved-getcampaigns-api-is-not-returning-ads-data) | Resolved |
| 2026-07-22 | [Sandbox: не создавались или не отображались keysets](https://developer.ebay.com/develop/get-started/api-status/sandbox/resolved-issue-with-sandbox-keyset-creation-and-visibility) | Resolved |
| 2026-07-22 | [GetMyMessages: API возвращал перевод вместо исходного текста](https://developer.ebay.com/develop/get-started/api-status/production/resolved-getmymessages-api-returns-auto-translated-message-text-while-web-ui-shows-original-text) | Resolved |
| 2026-07-15 | [Taxonomy: fetchItemAspects, HTTP 500](https://developer.ebay.com/develop/get-started/api-status/production/resolved-fetchitemaspects-taxonomy-api-is-returning-error-code-500) | Resolved |
| 2026-07-05 | [Trading: System Error при revise / verify](https://developer.ebay.com/develop/get-started/api-status/production/resolved-system-errors-in-revisefixedpriceitem-and-verifyaddfixedpriceitem-apis) | Resolved; описание ещё содержит старую фразу о работе над ошибкой |
| 2026-06-30 | [Finances: GetPayouts пропускал новые выплаты](https://developer.ebay.com/develop/get-started/api-status/production/resolved-getpayouts-api-missing-payouts-created-after-2026-06-26t08-27-54-000z) | Resolved |
| 2026-06-26 | [Trading: в GetOrders пропадал email покупателя](https://developer.ebay.com/develop/get-started/api-status/production/resolved-buyer-email-is-missing-in-the-getorders-response) | Resolved |
| 2026-06-17 | [Post-Order: Issue Return Refund, HTTP 500](https://developer.ebay.com/develop/get-started/api-status/production/resolved-post-order-api-issue-return-refund-returning-http-500-internal-server-error) | Resolved |
| 2026-06-15 | [Buy Feed: отсутствовали категории в snapshot-файлах](https://developer.ebay.com/develop/get-started/api-status/production/resolved-missing-categories-in-hourly-feed-19) | Resolved |
| 2026-06-10 | [Trading: ReviseItem / AddItem, ошибка 10007](https://developer.ebay.com/develop/get-started/api-status/production/resolved-trading-api-reviseitem-additem-call-returning-system-error) | Resolved |
| 2026-06-10 | [Trading: ошибка shipping rate table 21919487](https://developer.ebay.com/develop/get-started/api-status/production/resolved-trading-api-calls-are-failing-with-error-code-21919487) | Resolved |
| 2026-06-08 | [Сайт зависал на проверке браузера splashui](https://developer.ebay.com/develop/get-started/api-status/production/resolved-ebay-website-redirects-to-splashui-and-not-redirecting-back) | Resolved |
| 2026-06-07 | [EPS: истёк сертификат i.ebayimg.cn](https://developer.ebay.com/develop/get-started/api-status/production/resolved-cert-expired-for-i-ebayimg-cn) | Resolved |
| 2026-05-20 | [Sandbox: createFulfillmentPolicy, внутренняя ошибка 20500](https://developer.ebay.com/develop/get-started/api-status/sandbox/resolved-sandbox-internal-error-when-calling-createfulfillmentpolicy-api) | Resolved; старая Sandbox-таблица показывала Unresolved |
| 2026-05-18 | [Ошибка создания keyset](https://developer.ebay.com/develop/get-started/api-status/production/resolved-facing-issue-with-creating-keysets) | Resolved; карточка помечена Production, но описывает создание Sandbox keyset |
| 2026-05-15 | [Sandbox: переход на CDN и новые IP-диапазоны](https://developer.ebay.com/develop/get-started/api-status/sandbox/sandbox-ip-address-changes) | Resolved; изменение внедрено, при IP allowlist требуется учитывать новые диапазоны |

### 9.3. Что это меняет для BENIM

- В прочитанных карточках **не найден подтверждённый инцидент регистрации Sandbox с `sellerRegistrationCompleted=false`**. Сбой создания ключей, создание fulfillment policy и регистрация продавца — разные операции.
- Ошибки Lot Size и System Error не дают оснований менять товарные данные наугад: сначала сверять конкретный метод, окружение, ошибку, время и карточку инцидента. По более свежим карточкам эти два инцидента закрыты.
- Активная проблема Negotiation относится к предложениям заинтересованным покупателям. Она не является общим запретом на создание inventory item или offer и не объясняет наш результат getPrivileges.
- Уведомление о CDN актуально для IP allowlist. Успешный JSON-ответ getPrivileges с `false` уже показывает, что конкретный запрос достиг eBay; это не симптом заблокированного соединения.
- Пустой или неполный ответ не всегда означает отсутствие данных: история содержит инциденты пропуска выплат, рекламы и категорий. Перед разрушительными действиями проверять достоверность результата.

## 10. Deprecation: актуализация выбора API

Прочитаны обе таблицы страницы [API Deprecation Status](https://developer.ebay.com/develop/get-started/api-deprecation-status): запланированные отключения и уже отключённые возможности, включая группы Post-Order. Даты ниже трактуются относительно **16 октября 2026 года**.

Deprecated — устаревающая возможность; Decommissioned — отключённая. Прошедшая дата в верхней таблице не становится будущей только из-за места строки. TBD означает отсутствие объявленной даты, а не рекомендацию использовать API.

### 10.1. Основные сроки

| Возможность | Отключение / направление перехода |
| --- | --- |
| GeteBayDetails | 2027-03-15 → Metadata |
| ShoppingCartItemEndingSoon | 2027-01-11 |
| GetSellerDiscountProfiles / SetShippingDiscountProfiles | 2027-01-19 → Account v2 |
| VeRO v1 | 2026-09-30 → VeRO v2 |
| UploadSiteHostedPictures | 2026-09-30 → Media |
| Order v1: updatePaymentInfo | 2026-06-02 |
| Merchandising | TBD |
| Return Management | TBD → Post-Order |
| Legacy Business Policies Management | TBD → Account |
| GetAdFormatLeads | 2026-09-21 → Leads |
| ItemMarkedPaid event | 2026-06-22 |
| Product API | 2026-08-15 → Catalog / Metadata |
| GetCategoryFeatures | 2026-06-04 → Metadata |
| Product Metadata | 2026-04-27 → Metadata |
| GetCategories | 2026-04-15 → Taxonomy / Metadata |
| setupQuickCampaign / launchCampaign | 2026-03-31 |
| Compliance | 2026-03-30 |
| eBayPlus / eBayPlusEligible | 2026-02-16 |
| Legacy Trading VeRO | 2026-02-02 → актуальный VeRO |
| findCompatibilitiesBySpecification | 2025-08-11 → Metadata |
| ExtendSiteHostedPictures / ExtensionInDays | 2025-07-28 |
| GetCategoryMappings | 2025-06-02 → Taxonomy |
| Finding / Shopping | 2025-02-04 → Browse |
| Группы Post-Order | 2026-01-20, 02-02, 03-02, 03-16: проверять метод отдельно |

Источник всей таблицы: [официальный перечень](https://developer.ebay.com/develop/get-started/api-deprecation-status). Таблица не означает отключение всего Trading или всего Post-Order API.

### 10.2. Современные замены метаданных

В новой реализации **не строить получение вариантов доставки вокруг GeteBayDetails**. [Migration Guide](https://developer.ebay.com/api-docs/user-guides/static/trading-user-guide/migration-gebd.html) и [Metadata OpenAPI](https://developer.ebay.com/api-docs/master/sell/metadata/openapi/3/sell_metadata_v1_oas3.json) задают отдельные операции:

```http
GET /sell/metadata/v1/shipping/marketplace/EBAY_DE/get_shipping_services
GET /sell/metadata/v1/shipping/marketplace/EBAY_DE/get_shipping_carriers
GET /sell/metadata/v1/shipping/marketplace/EBAY_DE/get_handling_times
```

Также предусмотрены `getShippingLocations`, `getExcludeShippingLocations`, `getCurrencies` и другие специализированные методы. Сервисы возвращают данные о перевозке и ограничениях посылки; это не автоматически выбранная бизнес-политика нашего продавца. Для характеристик использовать Taxonomy, а для допустимых возвратов и состояний — соответствующие методы Metadata.

Отдельно изучены [миграция GetCategories](https://developer.ebay.com/api-docs/user-guides/static/trading-user-guide/migration-gc.html) и [миграция GetCategoryFeatures](https://developer.ebay.com/api-docs/user-guides/static/trading-user-guide/migration-gcf.html). Даже свежая обзорная страница может ссылаться на уже отключённый Legacy-метод.

### 10.3. Исправления прежних выводов

- Compliance больше не предлагается как будущий модуль BENIM: прежнее упоминание в разделе 3.13 исправлено.
- Для загрузки изображения при необходимости выбирать актуальные Media `createImageFromFile` / `createImageFromUrl`; прежний UploadSiteHostedPictures не закладывать в новую интеграцию. Внешние image URLs проверять по требованиям Inventory отдельно.
- Для политик оставаться на Account API, не путать его с устаревшим Business Policies Management API. Наша включённая программа `SELLING_POLICY_MANAGEMENT` — не отключённый API.
- При разработке возвратов нельзя копировать все операции старого руководства Post-Order: опубликованные группы содержат отключённые методы, в том числе операции с адресом, refund и labels. Сверять каждый выбранный endpoint с текущим справочником.
- Устаревшие методы из примеров WareHub не становятся подходящими для BENIM только потому, что они присутствуют в коде или старой инструкции.

## 11. Итог и что здесь не проверялось

**Обновление плана BENIM:** проверка типа политики исправлена; добавлено чтение категорий и характеристик. Управление политиками и warehouse отложено по решению владельца. Разработку можно продолжать, но готовность к публикации не объявлять до решения регистрации и требований конкретного товара.

При изучении источников менялся только этот документ; последующая реализация справочника отдельно описана в setup. Не выполнялись реальная авторизация, запись настроек eBay, создание складов или политик, публикация, удаление аккаунта, тестовые уведомления и проверки на сервере. Текущие квоты приложения, Production-аккаунты и возможность публикации при `sellerRegistrationCompleted=false` не подтверждены.
