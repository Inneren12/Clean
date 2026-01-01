# ПРОМПТ ДЛЯ CODEX: ПОЛНЫЙ АУДИТ ГОТОВНОСТИ К PRODUCTION

## ЗАДАЧА
Проведи полный комплексный аудит FastAPI приложения для клининг-сервиса и дай окончательный вердикт: **ГОТОВО** или **НЕ ГОТОВО** к production deployment.

---

## КОНТЕКСТ ПРИЛОЖЕНИЯ

Это SaaS multi-tenant платформа для клининг-сервиса с следующими компонентами:
- **Backend**: FastAPI + PostgreSQL + SQLAlchemy (async)
- **Архитектура**: Multi-tenant SaaS с JWT authentication
- **Интеграции**: Stripe (платежи), SendGrid/SMTP (email), S3 (хранение фото)
- **Порталы**: Admin UI, Worker Portal, Client Portal
- **Infrastructure**: Docker, Alembic migrations, Prometheus metrics, Jobs runner

---

## ОБЛАСТИ ПРОВЕРКИ (ПОЛНЫЙ СПИСОК)

### 1. ФУНКЦИОНАЛЬНАЯ ПОЛНОТА ✅

Проверь и задокументируй **ЧТО РЕАЛИЗОВАНО**:

#### 1.1 Core Business Features
- [ ] Pricing calculator (estimate endpoint)
- [ ] Chat session management
- [ ] Lead capture and pipeline management
- [ ] Slot search and booking creation
- [ ] Deposit requirements logic (weekend, deep clean, new clients)
- [ ] Stripe checkout session creation
- [ ] Webhook handling (checkout.session.completed, payment_intent.*)
- [ ] Booking lifecycle (pending → confirmed → done/cancelled)
- [ ] Invoice generation and payment processing
- [ ] Photo upload/download/delete with signed URLs
- [ ] Referral code system and credit tracking
- [ ] NPS surveys and support tickets

#### 1.2 Admin Portal
- [ ] Lead list/filter (by status: NEW, CONTACTED, BOOKED, DONE, CANCELLED)
- [ ] Lead status transitions
- [ ] Booking management (confirm, cancel, reschedule, complete)
- [ ] Worker CRUD operations
- [ ] Team management (working hours, blackouts)
- [ ] Dispatch assignment/unassignment
- [ ] Invoice send via email
- [ ] Manual payment recording
- [ ] Metrics dashboard (conversion rates, accuracy tracking)
- [ ] System health pages

#### 1.3 Worker Portal
- [ ] Login with BasicAuth + HMAC session cookies
- [ ] Jobs list (assigned to worker's team)
- [ ] Job detail view with booking info
- [ ] Time tracking (start, pause, resume, complete)
- [ ] Checklist completion
- [ ] Photo upload during service
- [ ] Add-ons management
- [ ] Dispute initiation
- [ ] NPS submission
- [ ] Support ticket creation

#### 1.4 Client Portal
- [ ] Magic-link authentication
- [ ] View orders list
- [ ] Booking create/reschedule/cancel
- [ ] Invoice viewing
- [ ] Photo viewing (with signed S3 URLs)
- [ ] Email notifications (booking confirmed, 24h reminder, completed)

#### 1.5 Background Jobs
- [ ] Email scan job (24h reminders, NPS surveys)
- [ ] Pending booking cleanup (30 min TTL)
- [ ] Data retention cleanup (chat sessions, old leads)
- [ ] Heartbeat monitoring
- [ ] Export to webhook/sheets (optional)

**ЗАДАНИЕ 1.1**: Перечисли все реализованные фичи с указанием файлов где они находятся.

**ЗАДАНИЕ 1.2**: Найди незавершенные TODO, FIXME, XXX комментарии в коде:
```bash
grep -rn "TODO\|FIXME\|XXX\|HACK" app/
```

**ЗАДАНИЕ 1.3**: Проверь наличие hardcoded URLs, example.com, placeholder значений:
```bash
grep -rn "example\.com\|placeholder\|CHANGEME\|YOUR_.*_HERE" app/
```

---

### 2. БЕЗОПАСНОСТЬ (CRITICAL) 🔒

#### 2.1 Authentication & Authorization

**ЗАДАНИЕ 2.1**: Проверь все методы аутентификации:
- [ ] JWT-based SaaS auth (`app/api/saas_auth.py`)
- [ ] Admin BasicAuth (`app/api/admin_auth.py`)
- [ ] Worker BasicAuth + session cookies (`app/api/worker_auth.py`)
- [ ] Client magic-link auth (`app/api/client_auth.py`)

Проверь:
1. Все ли секреты имеют безопасные дефолтные значения?
   - `AUTH_SECRET_KEY` - должен требовать установки в prod
   - `WORKER_PORTAL_SECRET` - не должен быть "worker-secret"
   - `CLIENT_PORTAL_SECRET` - не должен быть "dev-client-portal-secret"
   - `ADMIN_BASIC_PASSWORD` - должен требовать установки

2. Есть ли валидация конфигурации при `APP_ENV=prod`?
   ```python
   # Ищи функцию _validate_prod_config() в app/main.py
   ```

3. Password hashing использует bcrypt/Argon2 или только SHA256?
   ```bash
   grep -n "hashlib\|bcrypt\|argon2" app/infra/auth.py
   ```

**ЗАДАНИЕ 2.2**: Проверь RBAC (Role-Based Access Control):
```bash
# Найди все использования @require_owner, @require_admin, @require_dispatch
grep -rn "require_owner\|require_admin\|require_dispatch\|require_finance" app/api/
```

Убедись что:
- Все admin endpoints защищены
- Нет публичных endpoints без rate limiting
- Worker endpoints проверяют team_id ownership

#### 2.2 Multi-Tenant Data Isolation (КРИТИЧНО!)

**ЗАДАНИЕ 2.3**: Проверь изоляцию данных между организациями.

1. **Проверь наличие org_id в core tables**:
```sql
-- Выполни в psql или через код:
SELECT table_name, column_name
FROM information_schema.columns
WHERE column_name = 'org_id'
  AND table_schema = 'public'
ORDER BY table_name;
```

Таблицы которые ДОЛЖНЫ иметь org_id:
- [ ] bookings
- [ ] leads
- [ ] invoices
- [ ] workers
- [ ] teams
- [ ] orders
- [ ] chat_sessions

2. **Audit всех SQL queries**:
```bash
# Найди все select/update/delete без фильтрации по org_id
grep -rn "select(.*)" app/domain/*/service.py | grep -v "org_id"
```

3. **Проверь middleware установку org context**:
```python
# В app/api/admin_auth.py, app/api/worker_auth.py, app/api/saas_auth.py
# Должно быть: request.state.current_org_id = <org_id>
```

**ЗАДАНИЕ 2.4**: Проверь все API endpoints на org scoping:
```bash
# Список всех роутов
grep -rn "@router\." app/api/routes_*.py | wc -l
```

Для КАЖДОГО endpoint проверь:
- Есть ли проверка `org_id` перед возвратом данных?
- Можно ли получить доступ к данным другой организации через подстановку ID?

#### 2.3 Input Validation & Injection Prevention

**ЗАДАНИЕ 2.5**: Проверь защиту от инъекций:

1. **SQL Injection**:
```bash
# Найди raw SQL queries (должны использовать параметризацию)
grep -rn "session.execute(text(" app/
grep -rn "\.execute(f\"" app/
```

2. **XSS Protection**:
```bash
# В HTML templates должно быть автоматическое escaping
ls -la app/api/templates/*.html
grep -n "| safe\|mark_safe" app/api/templates/*.html
```

3. **CSRF Protection**:
```bash
# Проверь наличие CSRF middleware для form-based endpoints
grep -rn "CSRFProtect\|csrf_token" app/
```

4. **Command Injection**:
```bash
# Не должно быть os.system, subprocess без валидации
grep -rn "os\.system\|subprocess\.call\|subprocess\.run" app/
```

#### 2.4 Secrets Management

**ЗАДАНИЕ 2.6**: Проверь управление секретами:
```bash
# Не должно быть секретов в коде
grep -rn "sk_live_\|sk_test_\|whsec_\|password.*=.*['\"]" app/

# Проверь .env.example на placeholder значения
cat .env.example .env.production.example
```

#### 2.5 Rate Limiting & DoS Protection

**ЗАДАНИЕ 2.7**: Проверь rate limiting:

1. Проверь implementation:
```bash
cat app/infra/security.py
```

Вопросы:
- In-memory limiter использует locks для thread-safety?
- Redis limiter корректно обрабатывает unavailability (fail-open vs fail-closed)?
- Есть ли race conditions в `_requests` dictionary?

2. Проверь применение:
```bash
# Все публичные endpoints должны иметь rate limiting
grep -rn "rate_limit_middleware\|RateLimitMiddleware" app/main.py
```

#### 2.6 File Upload Security

**ЗАДАНИЕ 2.8**: Проверь безопасность загрузки файлов:

```python
# app/domain/bookings/photos_service.py
# app/api/routes_orders.py
```

Проверь:
- [ ] Ограничение размера файла (MAX_PHOTO_SIZE_MB)
- [ ] Проверка MIME type (не только по расширению)
- [ ] Sanitization имен файлов
- [ ] Signed URLs имеют expiration
- [ ] S3 bucket не публичный
- [ ] Нет path traversal уязвимостей

---

### 3. RELIABILITY & INFRASTRUCTURE 🏗️

#### 3.1 Database Connection Management

**ЗАДАНИЕ 3.1**: Проверь конфигурацию БД:
```python
# app/infra/db.py
```

Должно быть настроено:
- [ ] `pool_size` (рекомендуется 20)
- [ ] `max_overflow` (рекомендуется 10)
- [ ] `pool_timeout` (рекомендуется 30s)
- [ ] `pool_pre_ping=True` (для проверки соединений)
- [ ] Query timeout через `connect_args`

**ЗАДАНИЕ 3.2**: Проверь transaction management:
```bash
# Все write операции должны быть в транзакциях
grep -rn "session.add\|session.delete" app/domain/ | head -20
```

Проверь:
- Нет ли auto-commit режима?
- Используется ли `async with session.begin()`?
- Есть ли rollback при ошибках?

#### 3.2 External Service Reliability

**ЗАДАНИЕ 3.3**: Проверь таймауты для внешних сервисов:

1. **S3 / Storage**:
```python
# app/infra/storage/backends.py
# Должен быть Config(connect_timeout=..., read_timeout=...)
```

2. **SMTP / SendGrid**:
```python
# app/infra/email.py
# Должен быть socket timeout
```

3. **Stripe**:
```python
# Проверь stripe.api_timeout
grep -rn "stripe\." app/domain/payments/
```

**ЗАДАНИЕ 3.4**: Проверь retry logic:
```bash
# Должны быть retries для transient errors
grep -rn "retry\|backoff\|@retry" app/
```

**ЗАДАНИЕ 3.5**: Circuit Breakers:
```bash
# Опционально, но рекомендуется
grep -rn "circuit.*breaker\|CircuitBreaker" app/
```

#### 3.3 Job Runner Reliability

**ЗАДАНИЕ 3.6**: Проверь jobs runner:
```python
# app/jobs/run.py
# app/jobs/email_jobs.py
```

Проверь:
- [ ] Heartbeat recording работает (`/readyz` проверяет heartbeat)
- [ ] Graceful shutdown при SIGTERM
- [ ] Idempotency ключи для email jobs
- [ ] Обработка duplicate emails (race conditions)
- [ ] Dead letter queue для failed jobs (опционально)

**ЗАДАНИЕ 3.7**: Проверь email deduplication:
```sql
-- Должен быть unique constraint:
-- UNIQUE NULLS NOT DISTINCT (booking_id, invoice_id, email_type)
```

```bash
# Проверь миграции
grep -n "UNIQUE\|unique" alembic/versions/*.py | grep email_events
```

#### 3.4 Storage Consistency

**ЗАДАНИЕ 3.8**: Проверь порядок операций при delete:
```python
# app/domain/bookings/photos_service.py - delete_photo()
```

Правильный порядок:
1. Delete from database FIRST
2. Then delete from S3 (accept orphaned objects)

Неправильно:
1. Delete from S3 first → если DB delete fails, broken references

---

### 4. OBSERVABILITY & MONITORING 📊

#### 4.1 Health Checks

**ЗАДАНИЕ 4.1**: Проверь health endpoints:
```bash
curl http://localhost:8000/healthz
curl http://localhost:8000/readyz
```

`/readyz` должен проверять:
- [ ] Database connectivity
- [ ] Current migration version matches HEAD
- [ ] Jobs runner heartbeat (if JOB_HEARTBEAT_REQUIRED=true)

#### 4.2 Metrics

**ЗАДАНИЕ 4.2**: Проверь Prometheus metrics:
```python
# app/infra/metrics.py
# app/main.py - MetricsMiddleware
```

Проверь:
1. **Cardinality bomb protection**:
   - `http_5xx_total{method="...", path="..."}` - path должен быть route template, НЕ raw URL
   - Не должно быть unbounded labels (user_id, booking_id в labels)

2. **Metrics security**:
   - `/metrics` endpoint защищен Bearer token?
   - `METRICS_TOKEN` обязателен в production?

3. **Полезные метрики**:
```bash
curl http://localhost:8000/metrics | grep -E "^# TYPE"
```

Должны быть:
- Request counters (по endpoint, method)
- 5xx error counters
- Email job counters (success/error)
- Webhook event counters
- Jobs heartbeat timestamp

#### 4.3 Logging

**ЗАДАНИЕ 4.3**: Проверь логирование:
```python
# app/shared/logging_config.py или app/main.py
```

Проверь:
- [ ] Structured logging (JSON format в production)
- [ ] PII redaction (phone, email, addresses)
- [ ] Request ID tracking
- [ ] No raw request bodies в логах
- [ ] Appropriate log levels (ERROR для exceptions, INFO для бизнес-событий)

```bash
# Ищи logger.error с чувствительными данными
grep -rn "logger.*phone\|logger.*email" app/ | grep -v "redact\|mask"
```

---

### 5. COMPLIANCE & LEGAL ⚖️

#### 5.1 Email Compliance (CAN-SPAM, GDPR, CASL)

**ЗАДАНИЕ 5.1**: Проверь email compliance:

1. **Unsubscribe links**:
```bash
# Все marketing emails должны иметь unsubscribe
grep -rn "unsubscribe\|List-Unsubscribe" app/domain/notifications/
```

Проверь emails:
- [ ] NPS survey emails
- [ ] 24h reminder emails
- [ ] Marketing announcements

2. **Sender identity**:
```bash
# Проверь EMAIL_FROM, EMAIL_FROM_NAME
grep -n "EMAIL_FROM" app/settings.py
```

Не должно быть `noreply@example.com` в production.

3. **Preference center**:
```bash
# Опционально: table для email preferences
grep -rn "email_preferences\|EmailPreference" app/
```

#### 5.2 Data Retention

**ЗАДАНИЕ 5.2**: Проверь data retention policies:
```python
# app/jobs/email_jobs.py - cleanup_old_data_task
```

Проверь:
- [ ] `RETENTION_CHAT_DAYS` (default 30)
- [ ] `RETENTION_LEAD_DAYS` (default 365)
- [ ] `RETENTION_ENABLE_LEADS` (default false)
- [ ] Endpoint `/v1/admin/retention/cleanup` защищен

#### 5.3 GDPR / Privacy

**ЗАДАНИЕ 5.3**: Проверь GDPR compliance:
```bash
# Data subject access request (DSAR) support
grep -rn "gdpr\|data.*export\|right.*to.*be.*forgotten" app/
```

Минимальные требования:
- [ ] Возможность экспорта данных пользователя
- [ ] Возможность удаления данных (right to be forgotten)
- [ ] Privacy policy URL в emails
- [ ] Cookie consent (если есть web UI)

---

### 6. PERFORMANCE & SCALABILITY 🚀

#### 6.1 Database Performance

**ЗАДАНИЕ 6.1**: Проверь индексы:
```sql
-- Все foreign keys должны иметь индексы
-- Все поля используемые в WHERE должны иметь индексы
```

```bash
# Проверь миграции на CREATE INDEX
grep -rn "CREATE INDEX\|create_index" alembic/versions/
```

Обязательные индексы:
- [ ] `bookings(org_id)` - если есть org_id
- [ ] `leads(org_id)` - если есть org_id
- [ ] `bookings(starts_at)` - для slot search
- [ ] `email_events(booking_id, email_type)` - для deduplication

**ЗАДАНИЕ 6.2**: N+1 query проблемы:
```bash
# Ищи использование joinedload, selectinload
grep -rn "joinedload\|selectinload\|relationship" app/domain/*/service.py
```

Проверь популярные endpoints:
- `GET /v1/admin/leads` - должен использовать eager loading для related data
- `GET /v1/admin/bookings` - не должен делать N queries для teams

#### 6.2 Caching

**ЗАДАНИЕ 6.3**: Проверь кеширование:
```bash
# Опционально: Redis caching для pricing configs, slots
grep -rn "cache\|@lru_cache" app/
```

#### 6.3 Async Efficiency

**ЗАДАНИЕ 6.4**: Проверь async/await usage:
```bash
# Все I/O операции должны быть async
grep -rn "def " app/domain/*/service.py | grep -v "async def"
```

Synchronous operations блокируют event loop:
- `requests.get()` → должен быть `httpx.AsyncClient`
- `boto3` → должен использоваться в `run_in_executor()`
- `smtplib.SMTP()` → должен быть в `run_in_executor()`

---

### 7. TESTING 🧪

#### 7.1 Test Coverage

**ЗАДАНИЕ 7.1**: Запусти все тесты:
```bash
pytest -v --tb=short
```

Проверь:
- [ ] Все тесты проходят
- [ ] Нет warnings или deprecations
- [ ] Coverage > 70% (опционально: `pytest --cov=app`)

**ЗАДАНИЕ 7.2**: Проверь critical test scenarios:
```bash
ls tests/test_*.py
```

Должны быть тесты для:
- [ ] Multi-tenant data isolation (`test_org_isolation.py`)
- [ ] Authentication flows (JWT, BasicAuth, magic-link)
- [ ] Stripe webhook signature validation
- [ ] Rate limiting enforcement
- [ ] Email deduplication
- [ ] Storage operations (upload, delete, signed URLs)
- [ ] Booking lifecycle transitions
- [ ] Referral credit allocation

#### 7.2 Integration Tests

**ZADANIE 7.3**: Проверь integration tests:
```bash
# Тесты должны использовать реальную БД (testcontainers или test DB)
grep -rn "TestClient\|AsyncClient" tests/
```

#### 7.3 Security Tests

**ЗАДАНИЕ 7.4**: Negative security tests:
```bash
grep -rn "test.*unauthorized\|test.*forbidden\|test.*other.*org" tests/
```

Должны быть тесты:
- [ ] Нельзя получить leads другой организации
- [ ] Нельзя получить bookings другой организации
- [ ] Нельзя получить invoices другой организации
- [ ] Worker не видит jobs других teams
- [ ] Rate limit срабатывает после N requests

---

### 8. DEPLOYMENT READINESS 🚢

#### 8.1 Configuration Management

**ЗАДАНИЕ 8.1**: Проверь environment configuration:
```bash
cat .env.example
cat .env.production.example
```

Все критичные переменные должны быть documented:
- [ ] `DATABASE_URL`
- [ ] `AUTH_SECRET_KEY`
- [ ] `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET`
- [ ] `REDIS_URL` (для production)
- [ ] `S3_BUCKET` / `S3_REGION`
- [ ] `EMAIL_FROM` / `SENDGRID_API_KEY`
- [ ] `CORS_ORIGINS`
- [ ] `METRICS_TOKEN`

**ЗАДАНИЕ 8.2**: Проверь production validation:
```python
# app/main.py - должна быть функция _validate_prod_config()
```

При `APP_ENV=prod` должны проверяться:
- AUTH_SECRET_KEY != "dev-auth-secret"
- STRIPE_SECRET_KEY starts with "sk_live_"
- CORS_ORIGINS explicitly set
- REDIS_URL configured (не in-memory rate limiter)
- S3 configured (не local storage)

#### 8.2 Migrations

**ЗАДАНИЕ 8.3**: Проверь миграции:
```bash
# Список всех миграций
alembic history

# Текущая версия
alembic current

# Dry-run upgrade
alembic upgrade head --sql > migration_dry_run.sql
cat migration_dry_run.sql
```

Проверь:
- [ ] Все миграции have down-revision
- [ ] Нет conflicting heads
- [ ] Последняя миграция = 0034_org_id_uuid_and_default_org (или новее)

**ЗАДАНИЕ 8.4**: Проверь data migrations:
```bash
# Ищи data migrations (не только schema)
grep -rn "op.execute\|session.execute" alembic/versions/
```

Проверь:
- Есть ли backfill для org_id на существующих данных?
- Создается ли default organization?

#### 8.3 Docker & Deployment

**ЗАДАНИЕ 8.5**: Проверь Docker configuration:
```bash
cat Dockerfile
cat docker-compose.yml
```

Проверь:
- [ ] Multi-stage build для production
- [ ] Non-root user для security
- [ ] Health check в docker-compose
- [ ] Restart policy (restart: always)
- [ ] Volume для persistent data

**ЗАДАНИЕ 8.6**: Проверь graceful shutdown:
```python
# app/main.py должен обрабатывать SIGTERM
```

---

### 9. DOCUMENTATION 📚

**ЗАДАНИЕ 9.1**: Проверь наличие документации:
```bash
ls -la *.md docs/*.md
```

Должны быть:
- [ ] README.md с setup инструкциями
- [ ] API documentation (Swagger/OpenAPI)
- [ ] Deployment guide
- [ ] Security documentation
- [ ] Runbook для операторов

**ЗАДАНИЕ 9.2**: Проверь API documentation:
```bash
# OpenAPI schema должен генерироваться автоматически
curl http://localhost:8000/openapi.json | jq '.info'
```

---

## ИТОГОВЫЙ ОТЧЕТ

После выполнения всех заданий, создай **COMPREHENSIVE PRODUCTION READINESS REPORT** в формате:

### ФОРМАТ ОТЧЕТА

```markdown
# PRODUCTION READINESS AUDIT REPORT
**Application:** Cleaning Economy SaaS Platform
**Audit Date:** YYYY-MM-DD
**Auditor:** Codex AI

---

## EXECUTIVE SUMMARY

### VERDICT: ✅ READY / ❌ NOT READY / ⚠️ READY WITH CONDITIONS

**Summary:** [2-3 предложения о текущем состоянии]

### CRITICAL STATISTICS
- Tests: X passed / Y failed
- Security Issues: X critical, Y high, Z medium
- Blockers: X issues
- Warnings: Y issues
- Endpoints Reviewed: Z total

---

## 1. ФУНКЦИОНАЛЬНАЯ ПОЛНОТА

### Реализованные фичи
[Список всех работающих фичей с указанием файлов]

### Незавершенные фичи
[TODO, FIXME, hardcoded placeholders]

---

## 2. SECURITY ASSESSMENT

### Authentication & Authorization
[Результаты проверки auth системы]

### Multi-Tenant Isolation
[КРИТИЧНО: результаты audit org scoping]

**Data Isolation Score:** X/10

**Vulnerable Endpoints:**
- `GET /v1/admin/leads` - MISSING org_id filter
- `GET /v1/admin/bookings` - MISSING org_id filter
[полный список]

### Input Validation
[Результаты проверки инъекций]

### Secrets Management
[Проверка управления секретами]

---

## 3. RELIABILITY

### Database
[Connection pool, transactions, indexes]

### External Services
[Timeouts, retries, circuit breakers]

### Jobs Runner
[Heartbeat, idempotency, deduplication]

---

## 4. OBSERVABILITY

### Metrics
[Prometheus metrics, cardinality issues]

### Logging
[Structured logging, PII redaction]

### Health Checks
[/healthz, /readyz results]

---

## 5. COMPLIANCE

### Email Compliance
[CAN-SPAM, unsubscribe links]

### Data Retention
[GDPR, retention policies]

---

## 6. PERFORMANCE

### Database Performance
[Indexes, N+1 queries]

### Async Efficiency
[Event loop blocking]

---

## 7. TESTING

### Coverage: X%
### Passed: Y / Z tests

### Missing Critical Tests
[Список недостающих тестов]

---

## 8. BLOCKERS (MUST FIX BEFORE PRODUCTION)

### BLOCKER #1: [Title]
**Severity:** CRITICAL / HIGH / MEDIUM
**Impact:** [Описание влияния]
**Evidence:** [Файлы и строки кода]
**Fix Required:** [Конкретные шаги для исправления]

[Повторить для всех блокеров]

---

## 9. WARNINGS (FIX SOON AFTER LAUNCH)

[Аналогично блокерам, но с меньшей критичностью]

---

## 10. RECOMMENDATIONS

### Immediate Actions (Before Launch)
1. [Действие 1]
2. [Действие 2]

### Short-term (First Month)
1. [Действие 1]
2. [Действие 2]

### Long-term (Future Improvements)
1. [Действие 1]
2. [Действие 2]

---

## 11. RELEASE CHECKLIST

### Pre-Deployment
- [ ] All blockers fixed
- [ ] All tests passing
- [ ] Migrations tested
- [ ] Production secrets configured
- [ ] Database backed up

### Deployment
- [ ] Deploy to staging first
- [ ] Run smoke tests
- [ ] Monitor metrics for 24h
- [ ] Gradual rollout (10% → 50% → 100%)

### Post-Deployment
- [ ] Monitor error rates
- [ ] Check health endpoints
- [ ] Verify metrics collection
- [ ] Test critical user flows

---

## 12. CONCLUSION

[Финальный вердикт и рекомендации]

**Estimated Time to Production Ready:** X weeks / days
**Recommended Approach:** [Single-tenant / Multi-tenant / Phased rollout]

---

**Report Compiled By:** Codex Production Audit System
**Review Status:** COMPLETE
```

---

## ВАЖНЫЕ ЗАМЕЧАНИЯ ДЛЯ CODEX

1. **Будь максимально детальным**: Проверяй каждый файл, каждую миграцию, каждый endpoint.

2. **Используй автоматизацию**: Запускай grep, pytest, alembic команды для получения фактов.

3. **Не угадывай**: Если не можешь проверить что-то без запуска - укажи это как WARNING.

4. **Приоритизация**:
   - BLOCKER = Must fix before production (security, data loss risks)
   - WARNING = Should fix soon (reliability, compliance)
   - RECOMMENDATION = Nice to have (performance, maintainability)

5. **Конкретные доказательства**: Для каждого finding указывай:
   - Файл и номер строки
   - Код демонстрирующий проблему
   - Конкретные шаги для fix

6. **Реалистичный вердикт**: Не бойся давать NO-GO если есть критичные проблемы.

7. **Actionable recommendations**: Каждая рекомендация должна иметь четкие шаги для выполнения.

---

## НАЧНИ АУДИТ

Выполни все задания по порядку и создай финальный отчет.

**Да начнется аудит! 🚀**
