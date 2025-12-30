import urllib.parse
from typing import Any

from fastapi import Request

SUPPORTED_LANGS = {"en", "ru"}
DEFAULT_LANG = "en"

_TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "nav.dashboard": "Dashboard",
        "nav.my_jobs": "My Jobs",
        "worker.today": "Today",
        "worker.next_job": "Next job",
        "worker.no_jobs": "No jobs assigned.",
        "job.details_title": "Job",
        "job.starts_at": "Starts at",
        "job.duration": "Duration",
        "time.title": "Time tracking",
        "time.planned": "Planned",
        "time.actual": "Actual",
        "time.state": "State",
        "time.start": "Start",
        "time.pause": "Pause",
        "time.resume": "Resume",
        "time.finish": "Finish",
        "reasons.title": "Reasons",
        "reasons.none": "No reasons captured yet.",
        "scope.title": "Scope & notes",
        "scope.no_scope": "No scope captured.",
        "scope.customer_notes": "Customer notes",
        "addons.title": "Add-ons planned",
        "addons.planned": "Planned add-ons",
        "addons.add": "Add add-on",
        "addons.qty": "Quantity",
        "addons.none": "No add-ons planned.",
        "evidence.title": "Evidence required",
        "evidence.standard": "Standard before/after photos recommended.",
    },
    "ru": {
        "nav.dashboard": "Панель",
        "nav.my_jobs": "Мои заказы",
        "worker.today": "Сегодня",
        "worker.next_job": "Следующее задание",
        "worker.no_jobs": "Нет заданий",
        "job.details_title": "Детали задания",
        "job.starts_at": "Начало",
        "job.duration": "Длительность",
        "time.title": "Учёт времени",
        "time.planned": "План",
        "time.actual": "Факт",
        "time.state": "Статус",
        "time.start": "Начать",
        "time.pause": "Пауза",
        "time.resume": "Продолжить",
        "time.finish": "Завершить",
        "reasons.title": "Причины",
        "reasons.none": "Причины не зафиксированы.",
        "scope.title": "Объем работ",
        "scope.no_scope": "Объем не указан.",
        "scope.customer_notes": "Заметки клиента",
        "addons.title": "Дополнения",
        "addons.planned": "Запланированные дополнения",
        "addons.add": "Добавить дополнение",
        "addons.qty": "Количество",
        "addons.none": "Дополнения отсутствуют.",
        "evidence.title": "Фотоотчет",
        "evidence.standard": "Рекомендуются стандартные фото до/после.",
    },
}


def validate_lang(lang: str | None) -> str | None:
    if not lang:
        return None
    normalized = lang.strip().lower()
    if normalized.startswith("en"):
        return "en"
    if normalized.startswith("ru"):
        return "ru"
    return None


def _resolve_accept_language(header_value: str | None) -> str | None:
    if not header_value:
        return None
    for raw in header_value.split(","):
        candidate = validate_lang(raw.split(";", 1)[0])
        if candidate:
            return candidate
    return None


def resolve_lang(request: Request) -> str:
    cookie_lang = validate_lang(request.cookies.get("ui_lang"))
    if cookie_lang:
        request.state.ui_lang = cookie_lang
        return cookie_lang

    header_lang = _resolve_accept_language(request.headers.get("accept-language"))
    if header_lang:
        request.state.ui_lang = header_lang
        return header_lang

    request.state.ui_lang = DEFAULT_LANG
    return DEFAULT_LANG


def tr(lang: str | None, key: str, **fmt: Any) -> str:
    target_lang = validate_lang(lang) or DEFAULT_LANG
    template = _TRANSLATIONS.get(target_lang, {}).get(key)
    if template is None and target_lang != DEFAULT_LANG:
        template = _TRANSLATIONS.get(DEFAULT_LANG, {}).get(key)
    value = template if template is not None else key
    if fmt:
        try:
            value = value.format(**fmt)
        except Exception:
            return value
    return value


def _current_path(request: Request) -> str:
    filtered = [
        (key, value)
        for key, value in request.query_params.multi_items()
        if key.lower() not in {"lang", "ui_lang"}
    ]
    query = urllib.parse.urlencode(filtered, doseq=True)
    if query:
        return f"{request.url.path}?{query}"
    return request.url.path


def render_lang_toggle(request: Request, lang: str | None = None) -> str:
    current_lang = validate_lang(lang) or resolve_lang(request)
    next_path = _current_path(request)
    if not next_path.startswith("/"):
        next_path = "/"
    encoded_next = urllib.parse.quote(next_path, safe="/")
    links: list[str] = []
    for code, label in [("en", "EN"), ("ru", "RU")]:
        href = f"/ui/lang?lang={code}&next={encoded_next}"
        css_class = "lang-link lang-link-active" if current_lang == code else "lang-link"
        links.append(f'<a class="{css_class}" href="{href}">{label}</a>')
    return " | ".join(links)
