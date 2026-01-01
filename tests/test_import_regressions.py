"""
Regression tests for critical import paths.

These tests prevent "rename breaks app import" regressions by ensuring
that critical classes remain importable from their public API locations.
"""


def test_pricingconfig_import_from_models():
    """
    PricingConfig should be importable from app.domain.pricing.models.

    This is a backward compatibility requirement - even though the canonical
    implementation is in config_loader.py, models.py re-exports it for
    backward compatibility with existing code.
    """
    from app.domain.pricing.models import PricingConfig

    assert PricingConfig is not None
    assert hasattr(PricingConfig, "__name__")


def test_pricingconfig_import_from_config_loader():
    """
    PricingConfig should be importable from its canonical location.

    This is the actual implementation location in config_loader.py.
    """
    from app.domain.pricing.config_loader import PricingConfig

    assert PricingConfig is not None
    assert hasattr(PricingConfig, "__name__")


def test_app_main_imports_without_error():
    """
    The main app module should be importable without errors.

    This catches issues where internal imports break due to missing
    classes, circular imports, or other import-time errors.
    """
    from app.main import app

    assert app is not None
    assert hasattr(app, "router")


def test_routes_leads_imports_without_error():
    """
    The leads routes module should be importable without errors.

    This specifically tests that PricingConfig import works in routes_leads.
    """
    from app.api.routes_leads import router

    assert router is not None


def test_invoice_accepts_booking_id_parameter():
    """
    Invoice model should accept booking_id as a synonym for order_id.

    This is a backward compatibility requirement - even though the canonical
    field is order_id, booking_id should work via synonym.
    """
    from datetime import date
    from app.domain.invoices.db_models import Invoice
    from app.domain.invoices import statuses

    # Test that booking_id parameter is accepted
    invoice = Invoice(
        invoice_number="TEST-001",
        booking_id="test-booking-123",  # Using booking_id instead of order_id
        status=statuses.INVOICE_STATUS_DRAFT,
        issue_date=date.today(),
        currency="CAD",
        subtotal_cents=1000,
        tax_cents=50,
        total_cents=1050,
    )

    # Verify the synonym mapped correctly
    assert invoice.order_id == "test-booking-123"
    assert invoice.booking_id == "test-booking-123"


def test_invoice_public_token_accepts_token_parameter():
    """
    InvoicePublicToken should accept plaintext token and store hash.

    This tests backward compatibility for creating tokens with plaintext
    while maintaining security by storing only the hash.
    """
    import uuid
    from app.domain.invoices.db_models import InvoicePublicToken

    plaintext_token = uuid.uuid4().hex

    # Test that token parameter is accepted
    public_token = InvoicePublicToken(
        invoice_id="test-invoice-123",
        token=plaintext_token,  # Plaintext token provided
    )

    # Verify token is accessible right after creation
    assert public_token.token == plaintext_token

    # Verify token_hash is computed and stored
    assert public_token.token_hash is not None
    assert len(public_token.token_hash) == 64  # SHA256 hex = 64 chars


def test_local_storage_backend_has_upload_download_exists():
    """
    LocalStorageBackend should have upload/download/exists convenience methods.

    These methods provide a simpler API matching common usage patterns,
    wrapping the abstract put/read methods.
    """
    from app.infra.storage.backends import LocalStorageBackend

    # Verify the convenience methods exist
    storage = LocalStorageBackend(base_dir="/tmp/test_storage")

    assert hasattr(storage, "upload"), "LocalStorageBackend should have upload method"
    assert hasattr(storage, "download"), "LocalStorageBackend should have download method"
    assert hasattr(storage, "exists"), "LocalStorageBackend should have exists method"

    # Verify they're callable
    assert callable(storage.upload)
    assert callable(storage.download)
    assert callable(storage.exists)
