# Research notebooks

Notebooks here are **not importable**. They exist for exploration, EDA, and one-off
analysis. They are excluded from ruff/mypy/coverage. Every notebook must be
re-runnable top to bottom from a clean kernel.

## Rules

1. **Never define reusable logic in a notebook.** If a function might be useful
   twice, lift it into `src/supertrader/...` and import it back.
2. **First cell is always**:
   ```python
   %load_ext autoreload
   %autoreload 2
   import sys; sys.path.insert(0, '..')
   ```
3. **Last cell is empty.** Don't leak state.
4. **Outputs may be committed** if the notebook is reproducible and the outputs
   are small. Otherwise, clear outputs before committing.
5. **No secrets, no credentials, no large data inline.**
