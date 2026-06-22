# API Stability

pyNetScope follows semantic versioning.

## Public API

The supported public API is exported from `pynetscope.__init__` and documented
in the README. Public dataclass fields are treated as stable within a major
version unless marked experimental.

## Experimental API

Modules may expose lower-level helpers for testing or advanced integrations.
Names starting with `_` are private and may change in any release.

## Compatibility Policy

- Patch releases fix bugs without intentional breaking changes.
- Minor releases may add public APIs and optional fields.
- Major releases may remove or change APIs after a changelog notice.

