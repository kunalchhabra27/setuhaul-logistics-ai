# Check-In Portal Coding Standards

- Keep functions and classes small, explicit, and deterministic.
- Use docstrings for public modules, classes, and methods.
- Prefer `sqlite3.Row` reads that are converted into plain dictionaries at repository boundaries.
- Keep SQL statements parameterized and formatted for readability.
- Use type hints for inputs and return values on all new code.
- Preserve ASCII-only source files unless a file already uses non-ASCII text.
- Match the existing repository style: straightforward control flow, minimal abstraction, and no unnecessary framework code.
