"""TraceAudit AI — Custom SQLAlchemy types for cross-database compatibility."""

import json
from typing import Any
from sqlalchemy.types import Text, TypeDecorator


class JSONType(TypeDecorator):
    """Platform-independent JSON type.

    Stores JSON as Text/STRING in both SQLite and Databricks Delta Lake,
    and automatically serializes to/from Python dict/list objects.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (ValueError, TypeError):
                return value
        return value
