from fastapi import APIRouter, Depends

from app.domain.pricing.config_loader import PricingConfig
from app.domain.pricing.estimator import estimate
from app.domain.pricing.models import EstimateRequest, EstimateResponse
from app.dependencies import get_pricing_config

router = APIRouter()


@router.post("/v1/estimate", response_model=EstimateResponse)
async def create_estimate(
    request: EstimateRequest,
    pricing_config: PricingConfig = Depends(get_pricing_config),
) -> EstimateResponse:
    return estimate(request, pricing_config)
