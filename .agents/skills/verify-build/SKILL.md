# Verify Build
Run these checks in order and report failures:
1. `cd frontend && npm run build` — check TypeScript + bundle
2. `python -m py_compile $(git diff --name-only --diff-filter=AM | grep .py)` — syntax check changed Python files
3. `pytest tests/ -x -q` if tests directory exists
Stop on first failure and show the error.
