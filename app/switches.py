"""Launch switches (set in .env, default OFF). Admins always bypass them so features can be tested before launch.

  COURIER_ENABLED=true   courier delivery, courier registration and the courier pages
  ESCROW_ENABLED=true    pay-in-app escrow, MoMo payouts and the payments announcement
"""
import os


def _on(name: str) -> bool:
    return os.environ.get(name, "false").strip().lower() in ("1", "true", "yes", "on")


def escrow_enabled() -> bool:
    return _on("ESCROW_ENABLED")


def courier_enabled() -> bool:
    return _on("COURIER_ENABLED")


def courier_allowed(user) -> bool:
    return courier_enabled() or bool(getattr(user, "is_admin", False))


def escrow_allowed(user) -> bool:
    return escrow_enabled() or bool(getattr(user, "is_admin", False))
