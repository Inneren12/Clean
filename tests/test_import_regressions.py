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
