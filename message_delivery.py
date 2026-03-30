"""
Pure helpers for per-message delivery lifecycle state.

This module is intentionally UI-free so tests can validate routing/state
semantics without importing Qt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


DELIVERY_STATE_SENDING = "sending"
DELIVERY_STATE_QUEUED = "queued"
DELIVERY_STATE_DELIVERED = "delivered"
DELIVERY_STATE_FAILED = "failed"


@dataclass(frozen=True)
class DeliveryLifecycle:
    state: str
    route: str
    reason: str = ""
    hint: str = ""
    retryable: bool = False


def delivery_lifecycle_from_send_result(
    *,
    route: str,
    accepted: bool,
    reason: str = "",
    hint: str = "",
) -> DeliveryLifecycle:
    if accepted and route == "offline-queued":
        return DeliveryLifecycle(
            state=DELIVERY_STATE_QUEUED,
            route=route,
            reason=reason,
            hint=hint,
            retryable=False,
        )
    if accepted:
        return DeliveryLifecycle(
            state=DELIVERY_STATE_SENDING,
            route=route,
            reason=reason,
            hint=hint,
            retryable=False,
        )
    retryable = reason == "send-failed"
    return DeliveryLifecycle(
        state=DELIVERY_STATE_FAILED,
        route=route or "blocked",
        reason=reason,
        hint=hint,
        retryable=retryable,
    )


def delivery_state_label(state: Optional[str]) -> str:
    return {
        DELIVERY_STATE_SENDING: "Sending",
        DELIVERY_STATE_QUEUED: "Queued",
        DELIVERY_STATE_DELIVERED: "Delivered",
        DELIVERY_STATE_FAILED: "Failed",
    }.get((state or "").strip().lower(), "")


def normalize_loaded_delivery_state(state: Optional[str]) -> Optional[str]:
    normalized = (state or "").strip().lower()
    if not normalized:
        return None
    if normalized == DELIVERY_STATE_SENDING:
        return DELIVERY_STATE_FAILED
    if normalized in {
        DELIVERY_STATE_QUEUED,
        DELIVERY_STATE_DELIVERED,
        DELIVERY_STATE_FAILED,
    }:
        return normalized
    return None
