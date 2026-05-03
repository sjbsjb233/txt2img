"""Adapter abstract base class and registry.

Adapters bridge the normalized request / response shapes
(``app.schemas.normalized``) to a concrete upstream wire format. Each
adapter is a stateless singleton: every concrete subclass below
``BaseAdapter`` is instantiated once at startup by ``AdapterRegistry``
and re-used across all calls. Adapters MUST NOT carry per-request state
on ``self``.

Design doc references: §4.4 (interface), §4.4.2 (auto-discovery), §5.2
(normalized layer), §13.8 (admin listing).

Auto-discovery
--------------
``AdapterRegistry.discover()`` walks ``app.adapters`` (this package),
imports every Python module that doesn't start with ``_``, and registers
any non-abstract ``BaseAdapter`` subclass it finds. To add a custom
adapter at runtime, drop a new ``my_adapter.py`` into this directory,
restart the process, and ``GET /api/admin/adapters`` will surface it.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from abc import ABC, abstractmethod
from typing import ClassVar, Iterable

import httpx

from app.schemas.models import ModelUIField
from app.schemas.normalized import (
    NormalizedRequest,
    NormalizedResponse,
    ProviderConfig,
    StandardError,
    StandardErrorKind,
)
from app.schemas.provider import CapabilityField

logger = logging.getLogger("txt2img.adapters")


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class BaseAdapter(ABC):
    """Abstract base for all upstream adapters.

    Subclasses MUST set the three ``ClassVar`` strings below — they are
    treated as identity (``adapter_type``) and presentation
    (``display_name`` / ``description``) for the admin UI.

    Subclasses MUST implement ``generate`` and ``supported_models``. The
    default ``normalize_error`` covers transport failures; subclasses can
    override it to map upstream-specific error bodies before falling back
    to ``super().normalize_error``.
    """

    adapter_type: ClassVar[str] = ""
    display_name: ClassVar[str] = ""
    description: ClassVar[str] = ""

    @abstractmethod
    async def generate(
        self,
        provider: ProviderConfig,
        request: NormalizedRequest,
    ) -> NormalizedResponse:
        """Issue one upstream call and return the normalized result.

        Implementations should:

        - Validate ``request`` against fields the upstream cannot accept
          and raise ``StandardError(INVALID_PARAMETER, field=...)`` early.
          The executor's parameter check (PR-13) catches most of this,
          but adapters defend in depth because per-(provider, model)
          quirks live closest to the wire format.
        - Use ``httpx.AsyncClient(timeout=provider.timeout_seconds)`` so
          the executor's overall timeout budget is respected.
        - Translate any non-success upstream response or transport error
          into a ``StandardError`` via ``self.normalize_error``.
        - Return ``NormalizedResponse`` with at least ``images`` and
          ``image_count`` populated. ``raw`` should be a redacted dict
          suitable for ``json.dumps``.
        """

    @abstractmethod
    def supported_models(self) -> list[str]:
        """Return the model ids this adapter is capable of driving.

        This is the *theoretical* whitelist. Whether a given provider has
        that model enabled is a separate decision recorded in the
        ``provider_models`` table (design doc §4.1).
        """

    @abstractmethod
    def capability_schema(self) -> list[CapabilityField]:
        """Declare which capability fields admin may configure for this adapter.

        Returned by ``GET /api/admin/adapters`` and consumed by the
        provider editor UI. Each entry's ``k`` MUST match a field on
        ``ProviderModelCapabilities``; admin's saved ``capabilities_json``
        is still validated against that pydantic class.

        Convention:

        - ``CapabilityFieldList`` for whitelisted discrete values (chip
          multi-select). ``options`` should mirror the adapter's internal
          ``_ALLOWED_*`` constants so the UI never offers a value the
          adapter would reject at validation time.
        - ``CapabilityFieldInt`` for numeric upper bounds. ``min`` / ``max``
          mirror the ``ProviderModelCapabilities`` ``ge`` / ``le`` so the
          form can clamp before submission.
        - ``CapabilityFieldBool`` for tri-state opt-in flags.

        Adapters whose model variants differ in supported fields (e.g.
        Gemini 3 Pro vs 3.1 Flash) should surface the union here with
        ``help`` strings noting per-model limitations; the adapter's own
        ``_validate`` stays authoritative for actual rejections.
        """

    @abstractmethod
    def ui_schema(self, model_id: str) -> list[ModelUIField]:
        """Declare the Create-page parameter panel layout for ``model_id``.

        Returned field order, ``group``, and ``control`` decide how the
        Create page renders the right-hand panel; ``options`` is the
        adapter-side full set of candidate values, *not* what the user
        can currently reach. Reachability is computed in
        ``model_catalog._merge_capabilities`` and consumed by the
        frontend to grey out individual options instead of dropping the
        field — design v2 §3.

        Contract:

        - Each entry's ``k`` MUST match a field on
          :class:`app.schemas.models.ModelCapabilities`. Mismatched keys
          would yield a permanently-disabled control because the cap
          lookup would always be ``None``.
        - The result MUST NOT depend on user / tier / provider state. The
          schema is a property of the adapter + model only.
        - Multiple calls with the same ``model_id`` MUST return equal
          values (idempotent, no side effects).
        - For ``model_id`` outside ``supported_models()`` the
          implementation MUST raise ``ValueError``.

        Only the model's *primary adapter* (declared in
        ``model_catalog._MODEL_DISPLAY[..].primary_adapter``) is queried
        at runtime. Non-primary adapters still implement the method —
        admin previews and adapter self-tests use it — but their output
        does not flow into ``GET /api/models``.
        """

    # ------------------------------------------------------------------
    # Default error normalization
    # ------------------------------------------------------------------

    def normalize_error(self, exc: Exception) -> StandardError:
        """Translate a transport-layer exception into a ``StandardError``.

        Subclasses override this to handle their own response-body shape
        first, then fall back here for transport errors. Anything that
        can't be classified collapses to ``OTHER`` — never re-raise the
        original; the executor depends on a uniform exception type.
        """
        if isinstance(exc, StandardError):
            return exc
        if isinstance(exc, httpx.TimeoutException):
            return StandardError(
                StandardErrorKind.UPSTREAM_TIMEOUT,
                f"Upstream timed out: {exc}",
            )
        if isinstance(exc, httpx.ConnectError):
            return StandardError(
                StandardErrorKind.NETWORK_ERROR,
                f"Could not reach upstream: {exc}",
            )
        if isinstance(exc, httpx.HTTPError):
            return StandardError(
                StandardErrorKind.NETWORK_ERROR,
                f"HTTP transport error: {exc}",
            )
        return StandardError(
            StandardErrorKind.OTHER,
            f"Unhandled adapter error: {exc}",
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class AdapterRegistry:
    """Process-wide singleton mapping ``adapter_type`` → ``BaseAdapter``.

    Populated once at startup by ``discover()``. Routes that need to know
    which adapters exist (admin UI, provider validators) call ``list_all``
    or ``get`` on the singleton.
    """

    _instance: "AdapterRegistry | None" = None

    def __init__(self) -> None:
        self._adapters: dict[str, BaseAdapter] = {}

    # ------------------------------------------------------------------
    # Singleton accessors
    # ------------------------------------------------------------------

    @classmethod
    def instance(cls) -> "AdapterRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_for_tests(cls) -> None:
        """Drop the singleton so a test can re-discover from scratch."""
        cls._instance = None

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, adapter: BaseAdapter) -> None:
        """Register a single adapter instance.

        Raises ``ValueError`` if the ``adapter_type`` is empty, the class
        forgot to set ``display_name``, or the type is already registered.
        Strict checks here are deliberate: silently shadowing an existing
        adapter would be a security issue (a malicious file could replace
        the canonical openai_v1).
        """
        atype = adapter.adapter_type
        if not atype:
            raise ValueError(
                f"{type(adapter).__name__} forgot to set adapter_type"
            )
        if not adapter.display_name:
            raise ValueError(
                f"{type(adapter).__name__} forgot to set display_name"
            )
        if atype in self._adapters:
            raise ValueError(f"adapter_type {atype!r} already registered")
        self._adapters[atype] = adapter

    def discover(self, package_name: str = "app.adapters") -> None:
        """Walk ``app.adapters`` and register every non-abstract subclass.

        Module file names starting with ``_`` (including ``__init__``) are
        skipped. The ``base`` module is also skipped because it only
        contains the abstract class.
        """
        package = importlib.import_module(package_name)
        package_path: Iterable[str] = getattr(package, "__path__", [])
        for _finder, mod_name, _is_pkg in pkgutil.iter_modules(package_path):
            if mod_name.startswith("_") or mod_name == "base":
                continue
            full_name = f"{package_name}.{mod_name}"
            try:
                module = importlib.import_module(full_name)
            except Exception as exc:  # pragma: no cover — startup-only path
                logger.exception("failed to import adapter module %s: %s", full_name, exc)
                continue
            for attr_name in dir(module):
                obj = getattr(module, attr_name)
                if (
                    isinstance(obj, type)
                    and issubclass(obj, BaseAdapter)
                    and obj is not BaseAdapter
                    and not getattr(obj, "__abstractmethods__", None)
                ):
                    if obj.adapter_type in self._adapters:
                        # Already registered (e.g. the same class is
                        # imported twice via re-export); skip silently.
                        continue
                    try:
                        self.register(obj())
                        logger.info("registered adapter %s", obj.adapter_type)
                    except Exception as exc:  # pragma: no cover
                        logger.exception(
                            "failed to instantiate adapter %s: %s", full_name, exc
                        )

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def get(self, adapter_type: str) -> BaseAdapter:
        """Return the adapter or raise ``KeyError``.

        Callers that want a soft check should use ``has`` / ``list_all``
        first; the strict variant exists so a typo in a provider's
        ``adapter_type`` fails fast at request time.
        """
        try:
            return self._adapters[adapter_type]
        except KeyError as exc:
            raise KeyError(f"unknown adapter_type: {adapter_type!r}") from exc

    def has(self, adapter_type: str) -> bool:
        return adapter_type in self._adapters

    def list_all(self) -> list[BaseAdapter]:
        return list(self._adapters.values())


def get_registry() -> AdapterRegistry:
    """Convenience accessor for the process-wide registry."""
    return AdapterRegistry.instance()
