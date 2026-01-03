"""Central registry for SQLAlchemy models with string-based relationships.

Importing this module loads all ORM classes that may be referenced by string to
avoid mapper configuration errors when individual models are imported in
isolation.
"""

from app.domain.bookings import db_models as booking_db_models  # noqa: F401
from app.domain.subscriptions import db_models as subscription_db_models  # noqa: F401
from app.domain.workers import db_models as worker_db_models  # noqa: F401
from app.domain.invoices import db_models as invoice_db_models  # noqa: F401
from app.domain.saas import db_models as saas_db_models  # noqa: F401
from app.domain.ops import db_models as ops_db_models  # noqa: F401
from app.domain.notifications import db_models as notifications_db_models  # noqa: F401
from app.domain.outbox import db_models as outbox_db_models  # noqa: F401

