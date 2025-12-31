# Учебное пособие по порталам Worker и Admin

## Оглавление
- [Введение](#введение)
- [Доступ и вход](#доступ-и-вход)
- [Карта навигации](#карта-навигации)
- [Worker Portal — пошаговое обучение](#worker-portal--пошаговое-обучение)
- [Admin/Backoffice — пошаговое обучение](#adminbackoffice--пошаговое-обучение)
- [Типовые сценарии](#типовые-сценарии)
- [Troubleshooting (FAQ)](#troubleshooting-faq)
- [Глоссарий статусов и сущностей](#глоссарий-статусов-и-сущностей)
- [Приложение: быстрые шпаргалки](#приложение-быстрые-шпаргалки)

---

## Введение
- **Для кого:**
  - Работники (field crew) и тимлиды, использующие **Worker Portal**.
  - Диспетчеры/операторы и финансовая команда, использующие **Admin UI**.
  - Владелец/администратор, управляющий правами и аудитом.
  - Клиенты могут смотреть свои заказы через лёгкий клиентский кабинет (magic-link) — см. раздел «Доступ и вход».
- **Ключевые понятия (термины едины по всему документу):**
  - **Заказ/Job/Booking** — конкретный выезд с датой/временем и запланированной продолжительностью. Статусы включают PENDING, CONFIRMED, DONE, CANCELLED.
  - **Invoice (счёт)** — документ с позициями и платежами; статусы: DRAFT → SENT → PAID/PARTIAL/OVERDUE/VOID.
  - **Checklist** — шаблон задач по фазам (BEFORE/AFTER). Статусы выполнения: in_progress → completed.
  - **Time tracking** — хронометраж по состояниям NOT_STARTED, RUNNING, PAUSED, FINISHED.
  - **Dispute (спор/жалоба)** — процесс разбора проблемы: OPEN → FACTS_COLLECTED → DECIDED → CLOSED.
  - **Add-ons (доп. услуги/upsells)** — дополнительные услуги с ценой и минутами, влияющие на invoice.
  - **Reasons** — коды причин (TIME_OVERRUN, PRICE_ADJUST) с конкретными кодами и заметками.

## Доступ и вход
### Роли и права
| Роль | Что видит/делает |
| --- | --- |
| Owner | Полный контроль: создание/удаление пользователей, выдача/отзыв приглашений, выбор ролей, все административные действия. |
| Admin | Все операции админки и биллинга, может приглашать других (кроме владельца), управляет ролями. |
| Dispatcher | Admin UI/observability + диспетчеризация: подтверждение/завершение бронирований, time tracking/checklist. |
| Finance | Admin UI Invoices (листинг, детализация, ручные платежи), отчёты invoice/payments. |
| Viewer | Только просмотр административных экранов (observability, списки). |
| Worker | Worker Portal: Dashboard, My Jobs, Job details с time tracking, checklist, фото, add-ons, dispute/report issue. |
| Client | Мини-кабинет: список заказов, детали заказа, счёт, подписки, повтор заказа, отзыв. |

**SaaS модель и приглашения:**
- Владельцы и администраторы могут создавать приглашения из **Admin UI → Organization → Invites**. Приглашение содержит email, роль и токен, который истекает автоматически; повторная отправка идемпотентна.
- При переходе по ссылке вида `/accept-invite?token=…` пользователь создаёт учётную запись или привязывает существующую и получает membership в выбранной роли. Действие фиксируется в аудит-логе.
- Список активных участников и их ролей доступен на странице **Admin UI → Organization → Users**. Роли можно менять без перезапуска сервиса; все операции пишутся в журнал аудита.
- Работники теперь входят через обычную SaaS-аутентификацию (session cookie), а не через глобальный `WORKER_BASIC_*`. Старый basic режим можно оставить включённым флагом обратной совместимости.

### Вход в Worker Portal
1. **Basic Auth**: `POST /worker/login` — передаёт Basic в заголовке и ставит cookie `worker_session`.
2. **Основной вход:** после успешного логина открывайте `/worker` (Dashboard) и навигируйте по меню.
3. **Переменные окружения (обязательны):**
   - `WORKER_BASIC_USERNAME` / `WORKER_BASIC_PASSWORD`
   - `WORKER_TEAM_ID` — команда, чьи брони будут видны.
   - `WORKER_PORTAL_SECRET` — подпись session cookie.
4. **Сессии:** cookie `worker_session` читается middleware и даёт доступ ко всем `/worker/*` маршрутам.

### Вход в Admin/Backoffice
1. **Basic Auth**: все маршруты `/v1/admin/*` требуют Basic (owner/admin/dispatcher/accountant/viewer).
2. **Роли задаются переменными окружения** (по парам username/password): `OWNER_BASIC_*`, `ADMIN_BASIC_*`, `DISPATCHER_BASIC_*`, `ACCOUNTANT_BASIC_*`, `VIEWER_BASIC_*`.
3. **Навигация:** после авторизации доступна HTML-навигация `/v1/admin/observability` и `/v1/admin/ui/invoices`.
4. **Клиентский кабинет:** `/client/login/request` отправляет magic-link, cookie `client_session` открывает `/client` и связанные страницы.

## Карта навигации
### Схема экранов
```mermaid
graph TD
    W0[Worker Dashboard (/worker)] --> W1[My Jobs (/worker/jobs)]
    W1 --> W2[Job details (/worker/jobs/{id})]
    W2 --> W2A[Time tracking Start/Pause/Resume/Finish]
    W2 --> W2B[Checklist (GET/PATCH/complete)]
    W2 --> W2C[Photos before/after]
    W2 --> W2D[Add-ons form]
    W2 --> W2E[Dispute report]

    A0[Admin Observability (/v1/admin/observability)] --> A0a[Case detail (/cases/{id})]
    A0 --> A1[Invoices list (/v1/admin/ui/invoices)]
    A1 --> A2[Invoice detail (/v1/admin/ui/invoices/{id})]
```

### Быстрые переходы
- Worker топ-бар: **Dashboard** → `/worker`, **My Jobs** → `/worker/jobs`, карточки ведут на `/worker/jobs/{id}`.
- Внутри Job: формы POST на **Start/Pause/Resume/Finish**, отдельные формы для add-ons и завершения с reason-кодами.
- Admin: навигация в шапке между **Observability** и **Invoices**.

## Worker Portal — пошаговое обучение
### Экран «Dashboard» (`/worker`)
- Показывает счётчики заказов по статусу и «Next job» карточку ближайшего будущего заказа.
- Бейджи: статус заказа, **Risk** (если risk_band ≠ LOW), **Deposit** и статус invoice (если уже выписан).
- Типовые действия: перейти в My Jobs, открыть ближайший заказ, посмотреть риски/депозитные требования.

### Экран «My Jobs» (`/worker/jobs`)
- Список всех заказов команды с адресом, временем начала, длительностью и бейджами статуса/риска/депозита.
- Открытие деталей: клик по ID заказа ведёт на `/worker/jobs/{id}`.

### Экран «Job details» (`/worker/jobs/{id}`)
- Блоки:
  - **Time tracking** — состояние, плановые/фактические минуты, кнопки Start/Pause/Resume/Finish.
  - **Reasons** — список внесённых причин (время/цена) с кодом и заметкой.
  - **Policies & risk** — риск-бейдж, депозит, сводка отмены.
  - **Scope & notes** — структурированные inputs и заметки клиента.
  - **Add-ons planned** — текущие доп. услуги + форма добавления с выбором каталога и qty.
  - **Customer experience** — отправка опроса, badge ответа, заметки и статус тикета поддержки при низком NPS.
  - **Evidence required** — напоминания по рискам/депозиту и фото до/после.
- Приватность: работник видит контакт и адрес клиента, заметки и план работ; платежные данные и internal audit скрыты.

### Time tracking (Start/Pause/Resume/Finish)
1. **Старт**: нажмите **Start** — создаёт трекинг, состояние RUNNING.
2. **Пауза**: **Pause** — закрывает активный сегмент, состояние PAUSED. Ошибка 400 «Time tracking not started» если старт не был вызван.
3. **Продолжить**: **Resume** — состояние RUNNING. Ошибка 400 при попытке возобновить без старта.
4. **Завершить**: **Finish** — требует ввода причин, если превышен порог времени или нужна ценовая корректировка. Состояние FINISHED; повторный вызов вернёт «Already finished».
5. **Коды состояний:** NOT_STARTED (нет записи), RUNNING (идёт сегмент), PAUSED (временной останов), FINISHED (закрыто).
6. **Ошибки:**
   - 400 «Time tracking not started» — сначала нажмите Start.
   - 409 «Time tracking is paused, resume instead» — используйте Resume.
   - 400 при завершении без обязательной причины, если превышен порог `time_overrun_reason_threshold`.

### Reasons (причины)
- **TIME_OVERRUN_CODES:** ACCESS_DELAY, EXTRA_DIRT, CLIENT_REQUEST, SUPPLIES_MISSING, ESTIMATE_WRONG, PARKING_DELAY, OTHER.
- **PRICE_ADJUST_CODES:** ADDON_ADDED, DAMAGE_RISK, DISCOUNT_PROMO, CLIENT_COMPLAINT, EXTRA_SERVICE, OTHER.
- Хорошие примеры заметок: «Доступ дали на 15 минут позже, клиент предупредил», «Добавлен внутренний холодильник, +1 ед., согласовано устно».
- При выборе PRICE_ADJUST обязательно заполните текстовое поле note.

### Checklists
- **Открыть:** `GET /worker/jobs/{id}/checklist` создаёт run по авто-шаблону (service_type) или возвращает 404, если шаблон не найден.
- **Отметить пункт:** `PATCH /worker/jobs/{id}/checklist/items/{run_item_id}` с `checked`/`note`; конфликт 409, если пункт уже в финальном статусе.
- **Завершить:** `POST /worker/jobs/{id}/checklist/complete` — переводит статус run в `completed`.
- Если нет подходящего шаблона, отображается ошибка «Checklist not found».

### Photos (до/после)
- **Consent:** если `booking.consent_photos` = false, загрузка запрещена (403). Можно передать `consent=true` в форме, чтобы обновить согласие.
- **Загрузка:** `POST /worker/jobs/{id}/photos` с `phase=before|after` и файлом; неподдерживаемый тип или слишком большой размер вернёт 400/413.
- **Просмотр:** `GET /worker/jobs/{id}/photos` — список с phase, автором, временем.
- **Удаление:** `DELETE /worker/jobs/{id}/photos/{photo_id}` — только автор загрузки может удалить; иначе 403.

### Dispute / Report issue
- Используйте форму «Report issue» (`POST /worker/jobs/{id}/disputes/report`) при конфликте/жалобе клиента.
- Автоматически прикрепляются: ссылки на фото, snapshot чек-листа, time_log трекинга.
- После отправки спор виден админам; работник не принимает решение о возврате.

### Add-ons (Upsells)
- Форма «Add add-on» в Job details: выберите из каталога, задайте qty ≥ 1.
- Если invoice в статусе **DRAFT**, позиции синхронизируются в счёт автоматически; при **SENT/PAID/OVERDUE** требуется ручной пересмотр администратором.
- Рекомендации: фиксируйте в note причину доп. услуги и проговаривайте клиенту итоговую сумму.

## Admin/Backoffice — пошаговое обучение
### Общая навигация
- Топ-бар: **Observability** (`/v1/admin/observability`) и **Invoices** (`/v1/admin/ui/invoices`).
- Все страницы используют Basic Auth; роль **viewer** даёт только просмотр, **finance** — работу со счётами, **admin/owner** — полный доступ.

### Observability (`/v1/admin/observability`)
- **Назначение:** мониторинг лидов, эскалированных кейсов и диалогов бота.
- **Фильтры:** quick badges «Needs human», «Waiting for contact», «Order created»; кнопка Clear.
- **Карточки:**
  - Leads: статус, контакты, теги, дата создания.
  - Cases: summary, reason, ссылка «View detail» на `/cases/{case_id}`.
  - Dialogs: статус, последний месседж, время обновления.
- **Сценарии:**
  - Найти лид «waiting_for_contact» и передать диспетчеру.
  - Открыть case detail, посмотреть переписку/транскрипт и связаться с клиентом.

### Case detail (`/v1/admin/observability/cases/{id}`)
- Отображает контактные поля (телефон, email), транскрипт диалога, метаданные кейса.
- Ошибка 404, если кейс отсутствует.
- Используйте для разбора эскалаций и уточнения данных перед бронированием.

### Invoices list (`/v1/admin/ui/invoices`)
- **Назначение:** поиск/фильтр счетов, контроль баланса и overdue.
- **Фильтры:** статус, customer_id, order_id, поиск по номеру, пагинация.
- **Таблица:** invoice number (кликабельно), статус-бейдж, issue/due даты, total/paid/balance, связанный order/customer, created.
- **Сценарии:**
  - Отфильтровать OVERDUE, чтобы преследовать оплаты.
  - Найти счёт по номеру и открыть детализацию.

### Invoice detail (`/v1/admin/ui/invoices/{invoice_id}`)
- **Шапка:** номер, копирование ID/number, статус-бейдж, метрики Total/Paid/Balance/Due.
- **Клиент:** блок с именем, контактами, адресом и ID заказа.
- **Line items:** таблица описаний/qty/цены; показывает «No items recorded», если пусто.
- **Payments:** таблица с provider/method/amount/status/reference; кнопка «Record manual payment» для Cash/E-transfer/Card/Other.
- **Действия:** кнопка **Send invoice** (письмо с public link + PDF), отображение публичной ссылки после отправки.
- **Ошибки:** отправка невозможна без email клиента; запись платежа валидируется (amount > 0), неверные данные вернут 400.

### Дополнительные админ-функции (API)
- **Bookings:** `GET /v1/admin/bookings` с датами и статусами; `POST /v1/admin/bookings/{id}/confirm` — подтверждение, проверяет депозит.
- **Time tracking (dispatch):** `/v1/orders/{id}/time/*` — старт/пауза/возобновление/финиш с правами dispatch/admin.
- **Checklists:** управление шаблонами `/v1/admin/checklists/templates` (CRUD), запуск/обновление/complete для заказов `/v1/orders/{id}/checklist*`.
- **Add-ons каталог:** `/v1/admin/addons` CRUD, включая активность.
- **Reasons report:** `/v1/admin/reasons` (JSON/CSV) и `/v1/admin/reports/addons` — аналитика причин/допов.
- **Support tickets (NPS):** `/api/admin/tickets` список и PATCH статуса.

## Типовые сценарии
### Смена работника (выезд)
1. Зайти в **Dashboard**, открыть ближайший Job.
2. Нажать **Start** в Time tracking сразу по прибытии.
3. Пройти чек-лист: отметить пункты BEFORE, собрать фото «before» при необходимости.
4. Выполнить работу, при паузе (ожидание клиента/доступа) нажать **Pause**, затем **Resume**.
5. По завершении — добавить add-ons, если согласованы, затем **Finish** с кодом задержки (если превышено время) и заметкой.
6. Сделать фото «after», отправить **Report issue** при любых несоответствиях.

### Добавили доп. услугу на месте
1. В Job details → блок **Add-ons planned** → выбрать услугу и qty → **Add add-on**.
2. Если invoice в статусе DRAFT — строчки сразу попадут в счёт; иначе предупредить, что админ обновит счёт вручную.
3. Зафиксировать причину в PRICE_ADJUST note (например, EXTRA_SERVICE) и сообщить клиенту новую сумму.

### Клиент недоволен / спор
1. Собрать evidence: чек-лист (complete), фото до/после, завершённый time log.
2. Нажать **Report issue** — спор откроется с авто-фактами.
3. Дальше админ решает: NO_REFUND / PARTIAL_REFUND / FULL_REFUND / CREDIT_NOTE; работник ждёт решения и следует инструкциям.

## Troubleshooting (FAQ)
- **401/403:** проверьте Basic Auth или cookie; для фото убедитесь в `consent=true` при первой загрузке.
- **404 Job/Photo/Checklist not found:** заказ не принадлежит вашей команде или удалён; для checklist — нет шаблона.
- **Time tracking not started / paused:** всегда вызывайте Start перед Pause/Resume/Finish; при сообщении «paused, resume instead» нажмите Resume.
- **TIME_OVERRUN reason required:** при завершении с превышением плановых минут укажите один из TIME_OVERRUN_CODES.
- **Pricing adjustment note required:** при PRICE_ADJUST заполните текстовое поле; пустая заметка блокирует завершение.
- **Photo consent:** ошибка 403 при загрузке фото — установите чекбокс согласия или получите подтверждение клиента.

## Глоссарий статусов и сущностей
- **Booking:** PENDING (создан), CONFIRMED (подтверждён/оплачен депозит), DONE (завершён), CANCELLED (отменён); deposit_status: pending/paid/expired.
- **Invoice:** DRAFT, SENT, PAID, PARTIAL, OVERDUE, VOID; payment statuses SUCCEEDED/PENDING/FAILED; методы cash/etransfer/card/other.
- **Time tracking:** NOT_STARTED, RUNNING, PAUSED, FINISHED.
- **Checklist:** in_progress, completed; пункты имеют checked/checked_at/note.
- **Dispute:** OPEN → FACTS_COLLECTED → DECIDED → CLOSED; решения: no_refund, partial_refund, full_refund, credit_note.
- **Reasons:** TIME_OVERRUN_CODES (ACCESS_DELAY, EXTRA_DIRT, CLIENT_REQUEST, SUPPLIES_MISSING, ESTIMATE_WRONG, PARKING_DELAY, OTHER); PRICE_ADJUST_CODES (ADDON_ADDED, DAMAGE_RISK, DISCOUNT_PROMO, CLIENT_COMPLAINT, EXTRA_SERVICE, OTHER).
- **NPS/Support ticket:** статусы OPEN, IN_PROGRESS, RESOLVED (используются в админ PATCH).

## Приложение: быстрые шпаргалки
- **Чек-лист работника перед выездом:** проверить логин, открыть ближайший Job, стартовать трекинг, проверить чек-лист/допы, спросить про фото consent.
- **Чек-лист администратора по спору:** открыть dispute facts (фото, чек-лист, time log), связаться с клиентом, принять решение (refund/credit/none), зафиксировать в audit.
- **Когда использовать reason codes:**
  - ACCESS_DELAY/PARKING_DELAY — внешние задержки.
  - EXTRA_DIRT/CLIENT_REQUEST — дополнительный объём работ.
  - ESTIMATE_WRONG — неправильно оценён объём.
  - ADDON_ADDED/EXTRA_SERVICE — upsell; DISCOUNT_PROMO/CLIENT_COMPLAINT — снижение цены; DAMAGE_RISK — защита от повреждений; OTHER — только с развёрнутой заметкой.
