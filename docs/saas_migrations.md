# SaaS migrations playbook

Эти шаги помогают перевести уже работающую инсталляцию из однопользовательского режима к многотенантной модели с организациями.

## 1. Подготовить Default-организацию
1. Создайте запись `organizations` c понятным именем, например `Default`. Теперь рекомендуемый `org_id` фиксирован и равен `00000000-0000-0000-0000-000000000001` (миграция `0034_org_id_uuid_and_default_org` создаёт запись, если её нет, и добавляет биллинг-заглушку).
2. Для существующих администраторов создайте `users` и `memberships`, выдав роли `owner/admin/dispatcher/finance` по необходимости.
3. Обновите переменные окружения, чтобы новые токены JWT содержали `org_id` и совпадали с созданными пользователями.

## 2. Привязать существующие данные к org_id
1. Остановите приложение и сделайте бэкап базы данных.
2. **Миграция `0035_add_org_id_to_core_tables` автоматически выполняет:**
   - Добавляет столбец `org_id` (UUID) во все бизнес-таблицы
   - Устанавливает значение Default-организации (`00000000-0000-0000-0000-000000000001`) для всех существующих строк
   - **Удаляет server_default** чтобы предотвратить молчаливый fallback к DEFAULT_ORG_ID
   - Создаёт индексы по `org_id` для основных таблиц (bookings, invoices, workers, leads, teams и т.д.)
   - Создаёт композитные индексы для эффективных запросов (например, `org_id + status`, `org_id + created_at`)
   - Настраивает внешние ключи на `organizations.org_id` (БЕЗ каскадного удаления для безопасности)
3. Миграция обрабатывает **30+ таблиц** включая:
   - **Bookings**: teams, bookings, email_events, order_photos, team_working_hours, team_blackouts
   - **Leads**: chat_sessions, leads, referral_credits
   - **Invoices**: invoice_number_sequences, invoices, invoice_items, invoice_payments, stripe_events, invoice_public_tokens
   - **Workers**: workers
   - **Documents**: document_templates, documents
   - **Subscriptions**: subscriptions, subscription_addons
   - **Disputes**: disputes, financial_adjustment_events
   - **Admin/Audit**: admin_audit_logs
   - **Checklists**: checklist_templates, checklist_template_items, checklist_runs, checklist_run_items
   - **Addons**: addon_definitions, order_addons
   - **Clients**: client_users
4. Миграция `0034` также переводит связанные таблицы (`organization_billing`, `organization_usage_events`) на UUID-тип и пересобирает внешние ключи.

## 3. Прогнать миграции и проверить запросы
1. Выполните `alembic upgrade head` после обновления кода.
2. Проверьте, что все API-приложения добавляют `org_id` в создаваемые записи и фильтруют выборки по нему.
3. Убедитесь, что публичные токены (инвойсы, ссылки) учитывают `org_id` и не пересекаются между организациями.

## 4. Smoke-тест
1. Выполните быстрый сценарий: войти как админ, создать заказ, выставить инвойс и оплатить его в рамках новой организации.
2. Создайте вторую организацию и убедитесь, что данные первой не видны ни в админке, ни в воркер-портале.
3. Запустите `pytest -q`, чтобы проверить автоматические тесты из репозитория.

## 5. Роллбек-план
- Если миграция прошла неуспешно, восстановите БД из бэкапа.
- Проверьте, что конфигурация окружения указывает на правильные org_id и секреты JWT.

## Примечания
- Не изменяйте `package.json` и `package-lock.json` в рамках миграции.
- Публичный UI инвойсов остаётся на английском, даже после включения новых организаций.
- Миграция `0035` является **staged migration** (поэтапная): добавление nullable столбца → backfill → **DROP DEFAULT** → NOT NULL → FK → indexes. Это минимизирует downtime и предотвращает silent fallback.
- Rollback поддерживается через `alembic downgrade -1`, но будет **необратимым** для данных, созданных после миграции в контексте нескольких организаций.
