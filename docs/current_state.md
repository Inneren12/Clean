# Текущее состояние платформы

## Краткое резюме
- Работает воркер-портал с HTML-экранами: вход по Basic Auth/кукам, список работ, карточка работы, учёт времени, чек-листы, фотоотчёт и добавление аддонов.
- Админ-раздел на /v1/admin защищён Basic Auth с ролями и аудитом действий (POST/PUT/PATCH/DELETE).
- Реализованы домены: учёт времени, чек-листы, фото/консент, диспуты, аддоны с синхронизацией инвойсов, инвойсы с публичным токеном, события аналитики и аудит админов.
- I18n: переключение ru/en через куку ui_lang; подписи портала локализованы, но блок инвойса остаётся на английском.
- Изоляция команд: воркер видит и изменяет только заказы своей команды; админ middleware проверяет права.
- Инфраструктура: middleware для request id, rate limit, логирование запросов; запуск через Docker/Makefile, БД Postgres, миграции Alembic.

## Возможности по ролям
### Воркер
- Вход: POST /worker/login (Basic Auth) создаёт куку сессии `worker_session`.
- Экран /worker – дашборд с ближайшей работой и счётчиками статусов; аудит VIEW_DASHBOARD.【F:app/api/routes_worker.py†L686-L717】【F:tests/test_worker_portal.py†L89-L110】
- Экран /worker/jobs – список работ команды, отображает риск/депозит/инвойс; аудит VIEW_JOBS.【F:app/api/routes_worker.py†L719-L750】【F:tests/test_worker_portal.py†L103-L110】
- Карточка /worker/jobs/{id} – детали заказа, таймер, чек-лист, фотографии, аддоны, статус инвойса (надписи Invoice всегда на EN).【F:app/api/routes_worker.py†L751-L821】【F:tests/test_worker_portal.py†L132-L145】
- Учёт времени: start/pause/resume/finish с причинами (delay/price adjust), события аналитики и reason logs, требует соблюдения очередности (нельзя finish без start).【F:app/api/routes_worker.py†L822-L1007】【F:tests/test_worker_portal.py†L173-L219】【F:tests/test_worker_portal.py†L226-L239】
- Чек-листы: получить/создать run, отметить пункт, завершить; всё логируется в аудит.【F:app/api/routes_worker.py†L1034-L1075】【F:app/api/routes_worker.py†L1077-L1110】【F:app/api/routes_worker.py†L1112-L1133】
- Фото: POST /worker/jobs/{id}/photos сохраняет файл по фазе (before/after), учитывает consent, пишет email-ивенты; доступ только своей команде.【F:app/api/routes_worker.py†L1135-L1189】【F:tests/test_worker_portal.py†L238-L244】
- Аддоны: POST /worker/jobs/{id}/addons обновляет список, синхронизирует черновик инвойса и пересчитывает суммы/скидки/депозит; добавление чужой команды запрещено.【F:app/api/routes_worker.py†L749-L821】【F:tests/test_worker_portal.py†L250-L369】
- Диспут: POST /worker/jobs/{id}/disputes создаёт обращение с reason/note, включая вложенные данные (timelog, фото, чеклист).【F:app/api/routes_worker.py†L1218-L1252】【F:app/domain/disputes/service.py†L34-L97】
- Опрос NPS: POST /worker/jobs/{id}/nps и /support создают ответ/тикет для поддержки.【F:app/api/routes_worker.py†L1248-L1302】

### Админ/финансы
- Доступ к /v1/admin* по Basic Auth с ролями owner/admin/dispatcher/accountant/viewer; права проверяются middleware, аудит на мутациях (POST/PUT/PATCH/DELETE).【F:app/api/admin_auth.py†L18-L104】【F:app/api/admin_auth.py†L140-L188】
- Экраны/JSON: маршруты в routes_admin (инвойсы, обсервабилити). Инвойсы: список/создание/обновление статуса, отправка email/публичный токен. Аудитируется действие и before/after снапшоты.【F:app/api/routes_admin.py†L1-L260】【F:app/domain/invoices/service.py†L24-L179】
- Аддоны/прайсинг: через админ API можно редактировать определения аддонов, что влияет на воркер-портал (каталог).【F:app/domain/addons/service.py†L18-L154】
- Аналитика: события событий bookings/log_event вызываются из воркер таймлайна, записи в таблицу EventLog.【F:app/domain/analytics/service.py†L9-L73】【F:app/api/routes_worker.py†L864-L907】

### Клиент/публичные пользователи
- Публичный инвойс: /public/invoices/{token} отображает HTML с суммами/линиями, доступ по токену и статусу, только чтение (англ. подписи).【F:app/api/routes_public.py†L10-L84】【F:app/domain/invoices/service.py†L138-L179】
- Клиентский API: /v1/client/estimates, /v1/client/orders, /v1/client/bookings – JSON для клиентского фронта; поддержка UI языка через куку ui_lang и роут /ui/lang.{【F:app/api/routes_client.py†L1-L220】【F:app/api/routes_ui_lang.py†L1-L70】

## Карта экранов
- Worker: `/worker/login` → `/worker` → `/worker/jobs` → `/worker/jobs/{id}` → чек-лист `/worker/jobs/{id}/checklist` → загрузка фото `/worker/jobs/{id}/photos` → диспут `/worker/jobs/{id}/disputes`.
- Admin: `/v1/admin/invoices` (список) → `/v1/admin/invoices/{id}` (деталь) → действия обновления/отправки.
- Public: `/public/invoices/{token}` (просмотр счета).

## Каталог эндпоинтов
| Method | Path | Auth | Назначение | Правила |
| --- | --- | --- | --- | --- |
| POST | /worker/login | Basic (worker) | Создать куку сессии | Только настроенный воркер; dev допускает фикс. секрет.【F:app/api/routes_worker.py†L686-L705】 |
| GET | /worker, /worker/jobs, /worker/jobs/{id} | Cookie session | HTML портала | Ограничение по team_id, аудит просмотров.【F:app/api/routes_worker.py†L692-L821】 |
| POST | /worker/jobs/{id}/start| Cookie | Начать время | Ставит state STARTED, событие аналитики.【F:app/api/routes_worker.py†L822-L858】 |
| POST | /worker/jobs/{id}/pause| Cookie | Пауза | Требует STARTED/RESUMED; аудит/лог причин.【F:app/api/routes_worker.py†L859-L897】 |
| POST | /worker/jobs/{id}/resume| Cookie | Возобновить | Проверка очередности; событие.RESUMED.【F:app/api/routes_worker.py†L898-L936】 |
| POST | /worker/jobs/{id}/finish| Cookie | Завершить | Валидирует start, пишет reasons и price adjust note обязательна при корректировке цены.【F:app/api/routes_worker.py†L937-L1007】 |
| GET/PATCH/POST | /worker/jobs/{id}/checklist* | Cookie | Получить/изменить/закрыть чек-лист | Автосоздание run по типу сервиса, аудит пунктов.【F:app/api/routes_worker.py†L1034-L1133】 |
| POST | /worker/jobs/{id}/photos | Cookie | Загрузка фото | Требует consent_photos; сохраняет метаданные/EmailEvent.【F:app/api/routes_worker.py†L1135-L1189】 |
| POST | /worker/jobs/{id}/addons | Cookie | Обновить аддоны | Синк чернового инвойса, перерасчёт totals/discount/deposit.【F:app/api/routes_worker.py†L749-L821】 |
| POST | /worker/jobs/{id}/disputes | Cookie | Создать диспут | Собирает факты: timelog, фото, чек-лист, причины.【F:app/api/routes_worker.py†L1218-L1252】 |
| POST | /worker/jobs/{id}/nps, /support | Cookie | Опрос/тикет | Пишет в NpsResponse/SupportTicket.【F:app/api/routes_worker.py†L1248-L1302】 |
| GET | /v1/admin/invoices, /v1/admin/invoices/{id} | Basic (roles) | JSON по инвойсам | Роли с правами VIEW/FINANCE; аудит мутаций.【F:app/api/routes_admin.py†L1-L150】 |
| POST/PATCH | /v1/admin/invoices/{id}/send, /status | Basic finance | Отправка e-mail, смена статуса (draft→final).【F:app/api/routes_admin.py†L151-L260】 |
| GET | /public/invoices/{token} | Token | Публичный просмотр | Доступен при статусах, англ. подписи.【F:app/api/routes_public.py†L10-L84】 |
| POST | /ui/lang | Cookie | Установить язык UI | Кука ui_lang, default en.|【F:app/api/routes_ui_lang.py†L1-L70】 |

## Реализованные бизнес-правила
- Изоляция команд: выборка заказов и операций воркера фильтруется по team_id; чужие записи дают 404/403.【F:app/api/routes_worker.py†L783-L807】【F:tests/test_worker_portal.py†L148-L159】
- Аудит: AdminAuditMiddleware логирует все мутации /v1/admin, воркеровые действия пишутся через audit_service вручную (VIEW_*, WORKER_TIME_UPDATE, CHECKLIST_* и т.д.).【F:app/api/admin_auth.py†L164-L188】【F:app/api/routes_worker.py†L700-L717】
- I18n: resolve_lang читает куку ui_lang; ru/ en тексты в шаблонах; блок Invoice всегда на EN (тест).【F:app/infra/i18n.py†L1-L80】【F:tests/test_worker_portal.py†L132-L145】
- Инвойсы: статусы draft/final/void; публичный токен создаётся при генерации; все лейблы на публичной странице англ.; обновление аддонов перезаписывает черновик и пересчитывает totals/discount/deposit.【F:app/domain/invoices/statuses.py†L1-L40】【F:app/domain/invoices/service.py†L24-L179】【F:tests/test_worker_portal.py†L250-L333】
- Аддоны: список доступен воркеру из определений (active only); изменения сохраняются в OrderAddon, синк с InvoiceItem, поддержка скидок/депозитов; нельзя добавлять несуществующий или с qty<=0.【F:app/api/routes_worker.py†L749-L821】【F:app/domain/addons/service.py†L68-L154】
- Диспуты: при создании собирает time entries, фото, чек-лист, lead info в DisputeFacts для дальнейшей обработки.【F:app/domain/disputes/service.py†L34-L97】
- Учёт времени: строгая машина состояний STARTED→PAUSED/RESUMED→FINISHED; фиксируются actual_seconds, reasons (TIME_OVERRUN, PRICE_ADJUST), analytics events и admin audit записи.【F:app/domain/time_tracking/service.py†L18-L170】【F:tests/test_worker_portal.py†L173-L220】
- Английский для инвойсов: даже при ru языке подписи Invoice/Line items не переводятся (тест защищает правило).【F:tests/test_worker_portal.py†L132-L145】

## Что пока не реализовано
- Нет полноценной авторизации пользователей/сессий кроме Basic Auth и куки для воркеров; отсутствует UI для клиента/админа (большинство админ маршрутов — JSON).
- Нет интеграции платежей/Stripe (страницы заявлены, но клиенты не инициализируются в main.py).【F:app/main.py†L142-L150】
- Нет загрузки/хранения файлов в облаке — photos_service оперирует локальным путём; нет CDN.
- Нет полноценного мониторинга/алертов; логирование есть, но не настроены внешние сервисы.

## Ограничения/риски
- Аутентификация через Basic Auth (env переменные); отсутствие MFA/SSO.
- Секреты портала воркера обязательны в проде, иначе RuntimeError; в dev автозаглушка worker-secret.【F:app/api/worker_auth.py†L37-L77】
- Rate limit работает через in-memory лимитер; без внешнего стора при масштабировании может быть несогласованность.【F:app/main.py†L93-L116】
- Публичный инвойс доступен по токену без доп. защиты; токен создаётся автоматически при генерации инвойса.【F:app/domain/invoices/service.py†L138-L179】
- Отсутствуют резервные копии БД/хранилища в коде; миграции должны выполняться вручную (alembic).

## Где искать реализацию и тесты
- Точки входа и middleware: `app/main.py` (подключение всех роутеров, CORS, rate limit, аудит).【F:app/main.py†L136-L242】
- Воркер-портал: `app/api/routes_worker.py`, домены `app/domain/time_tracking/*`, `checklists/*`, `bookings/*`, `addons/*`, `disputes/*`, `nps/*`.
- Админ/финансы: `app/api/routes_admin.py`, `app/api/admin_auth.py`, домен `app/domain/invoices/*`, `app/domain/admin_audit/*`.
- Публичный/клиентский доступ: `app/api/routes_public.py`, `routes_client.py`, `routes_ui_lang.py`, `app/infra/i18n.py`.
- Тесты поведения: `tests/test_worker_portal.py`, `tests/test_worker_quality.py`, `tests/test_admin_ui_invoices.py`, `tests/test_ui_lang.py` подтверждают изоляцию команд, правила инвойсов и i18n.【F:tests/test_worker_portal.py†L89-L219】
