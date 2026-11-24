"""Single-shot text-to-SQL baseline: one LLM call with BIRD's fixed prompt, no loop, no retry."""

from __future__ import annotations

import re

_SQL_BLOCK = re.compile(r"```sql\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_ANY_BLOCK = re.compile(r"```[a-zA-Z]*\s*(.*?)```", re.DOTALL)


def extract_sql(response: str) -> str:
    """Return the SQL from the last ```sql block (or last fenced block); empty string if none.

    Leading ``--`` comment lines are kept (SQLite ignores them); a trailing semicolon is removed.
    """
    blocks = _SQL_BLOCK.findall(response) or _ANY_BLOCK.findall(response)
    if not blocks:
        return ""
    sql = str(blocks[-1]).strip()
    return sql.rstrip().rstrip(";").strip()
