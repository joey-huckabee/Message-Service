"""Port: read-only access to the registered template manifest.

Templates are not user-writable at runtime; they ship with the service
and are declared in ``templates.manifest_path``. This port exposes
lookup and existence checks for submission-time validation (L3-RUN-016,
L3-RUN-017).

Rendering itself is a separate concern: the template-rendering adapter
(in ``infrastructure/``) consumes the
:attr:`~message_service.domain.template_metadata.TemplateMetadata.source_path`
when it needs to actually produce HTML.

Requirement references
----------------------
L2-TMPL-001, L2-TMPL-002, L2-TMPL-003
L3-RUN-016, L3-RUN-017
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from message_service.domain.aggregates.template_metadata import TemplateKind, TemplateMetadata
from message_service.domain.aggregates.template_ref import TemplateRef


class TemplateRepository(ABC):
    """Abstract read-only lookup for registered templates.

    Implementations load the manifest at service start (or the first
    call, if lazy); they do not hot-reload (L2-TMPL-001). A restart is
    required to pick up manifest changes.

    Implementations MUST:

    * Raise
      :class:`~message_service.domain.errors.UnknownTemplateError` from
      :meth:`get` when a :class:`TemplateRef` is not in the manifest.
      ``details`` includes both ``name`` and ``version`` (L3-RUN-017) to
      distinguish "unknown template" from "unknown version".
    """

    @abstractmethod
    def get(self, ref: TemplateRef) -> TemplateMetadata:
        """Resolve a :class:`TemplateRef` to its manifest entry.

        Args:
            ref: The ``(name, version)`` pair referenced by a submitter.

        Returns:
            The manifest entry for the reference.

        Raises:
            UnknownTemplateError: The reference does not match any
                manifest entry. ``details`` includes ``name`` and
                ``version``.
        """

    @abstractmethod
    def exists(self, ref: TemplateRef) -> bool:
        """Return whether a reference is in the manifest.

        Preferred over catching :class:`UnknownTemplateError` when the
        caller only needs a boolean and the template's metadata is not
        required.

        Args:
            ref: The reference to check.

        Returns:
            ``True`` iff the reference is in the manifest.
        """

    @abstractmethod
    def resolve_latest(self, name: str) -> TemplateRef:
        """Return the highest-version :class:`TemplateRef` for ``name``.

        Used by :class:`BeginRunUseCase` to translate a request's
        literal `"latest"` sentinel into a concrete pinned version
        BEFORE the Run aggregate is constructed and persisted. After
        resolution the caller stores the returned :class:`TemplateRef`
        on the aggregate; subsequent manifest updates SHALL NOT
        retroactively affect already-initiated runs (L3-TMPL-009 +
        L3-TMPL-011 freeze the resolution at BeginRun time).

        Implementations SHALL compare candidate versions using
        :class:`packaging.version.Version` (per L2-TMPL-004) so
        pre-release semantics are honored — ``"1.0.0rc1"`` orders
        below ``"1.0.0"`` even though both are valid manifest
        entries. The returned version SHALL be the canonical
        ``str(Version(...))`` form per L3-TMPL-009.

        Args:
            name: The template name to resolve. Comparison is exact
                string match against
                :attr:`TemplateMetadata.name`; case-sensitive.

        Returns:
            A :class:`TemplateRef` with the highest-semver version
            among manifest entries sharing ``name``.

        Raises:
            UnknownTemplateError: No manifest entry matches the
                ``name``. ``details`` includes ``template_name``.
        """

    @abstractmethod
    def list_by_kind(self, kind: TemplateKind) -> Sequence[TemplateMetadata]:
        """List every registered template of the given kind.

        Used by administrative UI and by diagnostics. Order is
        unspecified.

        Args:
            kind: The kind to filter by.

        Returns:
            Sequence of metadata entries matching ``kind``.
        """

    @abstractmethod
    def list_all(self) -> Sequence[TemplateMetadata]:
        """List every registered template, ordered by (name, version).

        Used by the dashboard's template-registry inspection endpoint
        (L3-DASH-031). Order SHALL be deterministic — ascending by
        ``(name, version)`` — so callers can paginate or diff results
        without relying on adapter-internal storage order.

        Returns:
            Sequence of every manifest entry, ordered by
            ``(name, version)`` ascending.
        """


__all__ = ["TemplateRepository"]
