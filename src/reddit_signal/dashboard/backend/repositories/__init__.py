"""Dashboard persistence layer.

Repositories own SQL statements and transaction boundaries used by dashboard
features. Prefer returning plain dicts or dataclasses to FastAPI-specific
objects.
"""

