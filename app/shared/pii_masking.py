"""PII masking utilities for operator productivity endpoints."""

import re
from typing import Optional


def mask_email(email: Optional[str]) -> Optional[str]:
    """Mask email address: user@domain.com -> u***@domain.com"""
    if not email or "@" not in email:
        return email

    local, domain = email.split("@", 1)
    if len(local) <= 1:
        masked_local = "*"
    else:
        masked_local = local[0] + "***"

    return f"{masked_local}@{domain}"


def mask_phone(phone: Optional[str]) -> Optional[str]:
    """Mask phone number: 780-555-1234 -> 780-***-1234"""
    if not phone:
        return phone

    # Remove common separators to normalize
    normalized = re.sub(r"[^\d]", "", phone)

    if len(normalized) >= 10:
        # For North American format (10+ digits): show area code and last 4
        if len(normalized) == 10:
            # 7805551234 -> 780-***-1234
            return f"{normalized[:3]}-***-{normalized[-4:]}"
        elif len(normalized) == 11 and normalized[0] == "1":
            # 17805551234 -> 1-780-***-1234
            return f"1-{normalized[1:4]}-***-{normalized[-4:]}"
        else:
            # Other formats: show first 3 and last 4
            return f"{normalized[:3]}-***-{normalized[-4:]}"

    # Short numbers: just mask middle portion
    if len(normalized) >= 6:
        return f"{normalized[:2]}***{normalized[-2:]}"

    # Very short: mask all but first char
    return normalized[0] + "***" if normalized else phone


def mask_address(address: Optional[str]) -> Optional[str]:
    """Truncate address to first 20 chars with ellipsis."""
    if not address:
        return address

    if len(address) <= 20:
        return address

    return address[:20] + "..."


def truncate_sensitive_text(text: Optional[str], max_length: int = 50) -> Optional[str]:
    """Truncate sensitive text fields (notes, comments, bodies)."""
    if not text:
        return text

    if len(text) <= max_length:
        return text

    return text[:max_length] + "..."


def should_mask_pii(role: str) -> bool:
    """Determine if PII should be masked based on admin role.

    VIEWER roles should see masked PII.
    DISPATCH, FINANCE, ADMIN, OWNER see unmasked PII.
    """
    role_upper = role.upper()
    # Viewer gets masked data
    if role_upper == "VIEWER":
        return True

    # All other admin roles get unmasked data
    return False
