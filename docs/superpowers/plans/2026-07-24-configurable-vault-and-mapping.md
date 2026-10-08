# Configurable Vault & Profile-Mapping via `.env` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the 1Password vault name and profile-mapping file path configurable per machine via a `.env` file, and remove the hardcoded absolute path in `get_profile_mapping`.

**Architecture:** Add a tiny stdlib `.env` loader and pure resolution helpers to `aws_credential_manager/utils/config.py`. Configuration precedence is real env var > `.env` file > built-in default. All vault-default call sites are pointed at the shared `DEFAULT_VAULT` constant so `.env` controls them uniformly.

**Tech Stack:** Python 3.11+ standard library only (no new runtime dependencies), pytest.

## Global Constraints

- **Zero runtime dependencies** — loader and helpers must be stdlib-only. Do not add `python-dotenv` or any package to `[project].dependencies`.
- **Config precedence** — real environment variable > `.env` file value > built-in default. Real env vars must never be overwritten by the `.env` file.
- **Built-in defaults** — `DEFAULT_VAULT = "AWS"`, `PROFILE_MAPPING_FILE = "profile_mapping.json"`.
- **Env keys** — `DEFAULT_VAULT`, `PROFILE_MAPPING_FILE`.
- **Project root** — derive from `__file__` (`Path(__file__).resolve().parents[2]`), never hardcode a path.
- **Line length** — ruff limit is 100 chars (`pyproject.toml`).
- Run tests with: `python -m pytest tests/test_config.py -v` (or `-p no:cov` to skip coverage gating during a single-file run).

---

### Task 1: `.env` loader and config resolution helpers

**Files:**
- Modify: `aws_credential_manager/utils/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing (entry point of the feature).
- Produces:
  - `_load_dotenv(path: str, environ: dict | None = None) -> None` — parses a `.env` file, setting keys in `environ` (defaults to `os.environ`) only when absent.
  - `_resolve_mapping_path(filename: str, project_root: Path) -> str` — absolute filename returned as-is; relative filename joined to `project_root`.
  - Module constants `PROJECT_ROOT: Path`, `DEFAULT_VAULT: str`, `PROFILE_MAPPING_FILE: str`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py`:

```python
from pathlib import Path

from aws_credential_manager.utils.config import (
    PROJECT_ROOT,
    _load_dotenv,
    _resolve_mapping_path,
)


class TestDotenvLoader:
    def test_parses_key_value(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("DEFAULT_VAULT=Employee\n")
        env: dict[str, str] = {}
        _load_dotenv(str(env_file), env)
        assert env["DEFAULT_VAULT"] == "Employee"

    def test_skips_comments_and_blanks(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("# a comment\n\nPROFILE_MAPPING_FILE=x.json\n")
        env: dict[str, str] = {}
        _load_dotenv(str(env_file), env)
        assert env == {"PROFILE_MAPPING_FILE": "x.json"}

    def test_strips_quotes(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text('DEFAULT_VAULT="My Vault"\n')
        env: dict[str, str] = {}
        _load_dotenv(str(env_file), env)
        assert env["DEFAULT_VAULT"] == "My Vault"

    def test_does_not_override_existing(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("DEFAULT_VAULT=FromFile\n")
        env = {"DEFAULT_VAULT": "FromShell"}
        _load_dotenv(str(env_file), env)
        assert env["DEFAULT_VAULT"] == "FromShell"

    def test_tolerates_malformed_lines(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("no_equals_here\nGOOD=1\n")
        env: dict[str, str] = {}
        _load_dotenv(str(env_file), env)
        assert env == {"GOOD": "1"}

    def test_missing_file_is_noop(self, tmp_path: Path):
        env: dict[str, str] = {}
        _load_dotenv(str(tmp_path / "nope.env"), env)
        assert env == {}


class TestResolveMappingPath:
    def test_relative_joins_project_root(self):
        result = _resolve_mapping_path("profile_mapping.json", PROJECT_ROOT)
        assert result == str(PROJECT_ROOT / "profile_mapping.json")

    def test_absolute_returned_as_is(self, tmp_path: Path):
        abs_path = str(tmp_path / "custom.json")
        assert _resolve_mapping_path(abs_path, PROJECT_ROOT) == abs_path
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config.py::TestDotenvLoader tests/test_config.py::TestResolveMappingPath -v -p no:cov`
Expected: FAIL with `ImportError: cannot import name '_load_dotenv'`.

- [ ] **Step 3: Implement the loader, helpers, and constants**

Edit the top of `aws_credential_manager/utils/config.py`. Replace the existing imports/constants block (lines 1-17) with:

```python
"""Configuration management for AWS credential manager."""

import configparser
import json
import os
from pathlib import Path

# Project root = repository root (two levels up from this file:
# aws_credential_manager/utils/config.py -> repo root).
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: str, environ: dict | None = None) -> None:
    """Load KEY=VALUE lines from a .env file into environ (default os.environ).

    Only sets a key when it is not already present, so real environment
    variables always take precedence. Best-effort: malformed lines and a
    missing file are ignored silently.
    """
    env = os.environ if environ is None else environ
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in env:
                env[key] = value


def _resolve_mapping_path(filename: str, project_root: Path) -> str:
    """Resolve a mapping filename: absolute as-is, relative under project_root."""
    p = Path(filename)
    if p.is_absolute():
        return str(p)
    return str(project_root / p)


# Load .env at import so env vars are available before constants resolve.
_load_dotenv(str(PROJECT_ROOT / ".env"))

# Default expiry thresholds (days)
DEFAULT_PASSWORD_MAX_AGE = 90
DEFAULT_ACCESS_KEY_MAX_AGE = 90

# Default vault name (env-overridable; built-in default matches docs).
DEFAULT_VAULT = os.environ.get("DEFAULT_VAULT", "AWS")

# Profile mapping filename (env-overridable).
PROFILE_MAPPING_FILE = os.environ.get("PROFILE_MAPPING_FILE", "profile_mapping.json")

# Password generation settings
DEFAULT_PASSWORD_LENGTH = 18
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py::TestDotenvLoader tests/test_config.py::TestResolveMappingPath -v -p no:cov`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add aws_credential_manager/utils/config.py tests/test_config.py
git commit -m "feat: add stdlib .env loader and config resolution helpers"
```

---

### Task 2: Rewrite `get_profile_mapping`

**Files:**
- Modify: `aws_credential_manager/utils/config.py` (the `get_profile_mapping` method + add `get_mapping_path`)
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `_resolve_mapping_path`, `PROFILE_MAPPING_FILE`, `PROJECT_ROOT` from Task 1.
- Produces:
  - `ConfigManager.get_mapping_path(self) -> str` — resolved mapping file path, reading `PROFILE_MAPPING_FILE` env at call time (falls back to the module default).
  - `ConfigManager.get_profile_mapping(self, profile_name: str) -> dict | None` — unchanged signature/behavior, no hardcoded path.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py`:

```python
import json


class TestGetProfileMapping:
    def _write_mapping(self, tmp_path: Path) -> Path:
        data = {
            "profile_mappings": {
                "prof-a": {"onepassword_title": "AWS Prof A", "description": "x"}
            }
        }
        f = tmp_path / "profile_mapping.json"
        f.write_text(json.dumps(data))
        return f

    def test_returns_mapping_for_known_profile(self, tmp_path, monkeypatch):
        f = self._write_mapping(tmp_path)
        monkeypatch.setenv("PROFILE_MAPPING_FILE", str(f))
        cfg = ConfigManager()
        result = cfg.get_profile_mapping("prof-a")
        assert result == {"onepassword_title": "AWS Prof A", "description": "x"}

    def test_returns_none_for_unknown_profile(self, tmp_path, monkeypatch):
        f = self._write_mapping(tmp_path)
        monkeypatch.setenv("PROFILE_MAPPING_FILE", str(f))
        cfg = ConfigManager()
        assert cfg.get_profile_mapping("missing") is None

    def test_returns_none_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PROFILE_MAPPING_FILE", str(tmp_path / "nope.json"))
        cfg = ConfigManager()
        assert cfg.get_profile_mapping("prof-a") is None

    def test_returns_none_on_invalid_json(self, tmp_path, monkeypatch):
        f = tmp_path / "profile_mapping.json"
        f.write_text("{ not valid json")
        monkeypatch.setenv("PROFILE_MAPPING_FILE", str(f))
        cfg = ConfigManager()
        assert cfg.get_profile_mapping("prof-a") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config.py::TestGetProfileMapping -v -p no:cov`
Expected: FAIL — the current `get_profile_mapping` reads the old hardcoded path, so `test_returns_mapping_for_known_profile` returns `None` instead of the dict.

- [ ] **Step 3: Rewrite the method**

In `aws_credential_manager/utils/config.py`, replace the entire current `get_profile_mapping` method (its body including the dead code after `return`) with:

```python
    def get_mapping_path(self) -> str:
        """Resolve the profile mapping file path (env-overridable)."""
        filename = os.environ.get("PROFILE_MAPPING_FILE", PROFILE_MAPPING_FILE)
        return _resolve_mapping_path(filename, PROJECT_ROOT)

    def get_profile_mapping(self, profile_name: str) -> dict | None:
        """Return the 1Password mapping entry for a profile, or None."""
        mapping_path = self.get_mapping_path()
        if not os.path.exists(mapping_path):
            print(f"⚠ Profile mapping file not found: {mapping_path}")
            return None
        try:
            with open(mapping_path, encoding="utf-8") as f:
                mapping_data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"⚠ Error parsing profile mapping file: {e}")
            return None
        profile_mappings = mapping_data.get("profile_mappings", {})
        return profile_mappings.get(profile_name)
```

Also remove the now-duplicate `import json` inside the file if one lingers (json is imported once at the top after Task 1).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v -p no:cov`
Expected: PASS (all config tests, including the pre-existing ones).

- [ ] **Step 5: Commit**

```bash
git add aws_credential_manager/utils/config.py tests/test_config.py
git commit -m "fix: resolve profile mapping path via config instead of hardcoded path"
```

---

### Task 3: Unify vault defaults across call sites

**Files:**
- Modify: `aws_credential_manager/integrations/onepassword.py:15`
- Modify: `aws_credential_manager/core/credential_manager.py:21`
- Modify: `aws_credential_manager/cli/main.py:15-17`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `DEFAULT_VAULT` from `aws_credential_manager.utils.config` (Task 1).
- Produces: all three call sites default to the shared `DEFAULT_VAULT`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
class TestVaultDefaultsUnified:
    def test_onepassword_uses_shared_default(self):
        from aws_credential_manager.integrations.onepassword import OnePasswordClient
        from aws_credential_manager.utils.config import DEFAULT_VAULT

        assert OnePasswordClient().vault_name == DEFAULT_VAULT

    def test_credential_manager_uses_shared_default(self):
        import inspect

        from aws_credential_manager.core.credential_manager import CredentialManager
        from aws_credential_manager.utils.config import DEFAULT_VAULT

        sig = inspect.signature(CredentialManager.__init__)
        assert sig.parameters["vault_name"].default == DEFAULT_VAULT
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config.py::TestVaultDefaultsUnified -v -p no:cov`
Expected: FAIL — `OnePasswordClient` defaults to literal `"AWS"` and `CredentialManager` to literal `"Employee"`, which will not equal `DEFAULT_VAULT` once `.env` sets it to `Employee` / `AWS` respectively.

- [ ] **Step 3: Point each call site at `DEFAULT_VAULT`**

In `aws_credential_manager/integrations/onepassword.py`, add the import near the top (with the other imports) and change the default:

```python
from aws_credential_manager.utils.config import DEFAULT_VAULT
```
```python
    def __init__(self, vault_name: str = DEFAULT_VAULT):
        self.vault_name = vault_name
```

In `aws_credential_manager/core/credential_manager.py`, change line 21. It already imports from `..utils` — ensure `DEFAULT_VAULT` is imported (e.g. `from ..utils.config import ConfigManager, DEFAULT_VAULT` or add to the existing utils import), then:

```python
    def __init__(self, credentials_path: str | None = None, vault_name: str = DEFAULT_VAULT):
```

In `aws_credential_manager/cli/main.py`, import `DEFAULT_VAULT` from the config module and make the argument default/help dynamic (replace lines 15-17):

```python
    parser.add_argument(
        '--vault',
        default=DEFAULT_VAULT,
        help=f'1Password vault name (default: {DEFAULT_VAULT})',
    )
```

- [ ] **Step 4: Run the full test suite to verify it passes**

Run: `python -m pytest tests/ -v -p no:cov`
Expected: PASS (no regressions across all test files).

- [ ] **Step 5: Commit**

```bash
git add aws_credential_manager/integrations/onepassword.py aws_credential_manager/core/credential_manager.py aws_credential_manager/cli/main.py tests/test_config.py
git commit -m "refactor: unify vault default across call sites via DEFAULT_VAULT"
```

---

### Task 4: Supporting files — `.env.example` and this machine's `.env`

**Files:**
- Create: `.env.example`
- Create: `.env` (git-ignored — created locally, not committed)

**Interfaces:**
- Consumes: env keys `DEFAULT_VAULT`, `PROFILE_MAPPING_FILE` (Task 1).
- Produces: documented example config + working local override.

- [ ] **Step 1: Confirm `.env` is git-ignored (already present)**

Run: `git check-ignore .env`
Expected: prints `.env` (it is listed at `.gitignore:19`). If it prints nothing, add `.env` to `.gitignore`.

- [ ] **Step 2: Create `.env.example`**

Create `.env.example`:

```bash
# AWS Credential Manager — local configuration.
# Copy to .env and adjust for your machine / 1Password setup.
# Real shell environment variables override these values.

# 1Password vault that holds the AWS items (built-in default: AWS)
DEFAULT_VAULT=AWS

# Profile mapping file. Relative paths resolve against the project root;
# absolute paths are used as-is. (built-in default: profile_mapping.json)
PROFILE_MAPPING_FILE=profile_mapping.json
```

- [ ] **Step 3: Create this machine's `.env`**

Create `.env` (not committed):

```bash
DEFAULT_VAULT=Employee
```

- [ ] **Step 4: Verify the override works end-to-end**

Run: `python -c "from aws_credential_manager.utils.config import DEFAULT_VAULT; print(DEFAULT_VAULT)"`
Expected: prints `Employee` (from `.env`).

Run: `DEFAULT_VAULT=Shell python -c "from aws_credential_manager.utils.config import DEFAULT_VAULT; print(DEFAULT_VAULT)"`
Expected: prints `Shell` (real env var wins over `.env`).

- [ ] **Step 5: Commit the example file only**

```bash
git add .env.example
git commit -m "docs: add .env.example for vault and mapping configuration"
```

---

### Task 5: Update documentation

**Files:**
- Modify: `CLAUDE.md` (Vault Configuration + Default Thresholds sections)
- Modify: `README.md` (configuration section, if it documents the vault)

**Interfaces:**
- Consumes: behavior from Tasks 1-4.
- Produces: docs describing `.env` configuration and the `AWS` built-in default.

- [ ] **Step 1: Update `CLAUDE.md`**

In the "Vault Configuration" section, document the new precedence and keys. Add a note under configuration:

```markdown
### Environment Configuration (`.env`)
Copy `.env.example` to `.env` to configure per-machine settings. Precedence:
real environment variable > `.env` file > built-in default.
- `DEFAULT_VAULT` — 1Password vault name (built-in default: `AWS`)
- `PROFILE_MAPPING_FILE` — profile mapping file; relative paths resolve
  against the project root (built-in default: `profile_mapping.json`)
```

- [ ] **Step 2: Update `README.md`**

Add the same `.env` configuration note to the README's setup/configuration section (mirror the wording above so the two stay consistent).

- [ ] **Step 3: Verify no stale hardcoded path references remain**

Run: `grep -rn "/home/victor" aws_credential_manager/ CLAUDE.md README.md`
Expected: no matches.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: document .env configuration for vault and mapping file"
```

---

## Self-Review

**Spec coverage:**
- Tiny stdlib `.env` loader → Task 1. ✅
- Config precedence (env > .env > default) → Task 1 (`test_does_not_override_existing`) + Task 4 (end-to-end verify). ✅
- Project root from `__file__`, not hardcoded → Task 1 (`PROJECT_ROOT`). ✅
- `DEFAULT_VAULT` default `AWS`, env-overridable → Task 1. ✅
- `PROFILE_MAPPING_FILE` default + absolute/relative resolution → Task 1 (`_resolve_mapping_path`). ✅
- Rewrite `get_profile_mapping`, drop hardcoded path + dead code, check file not folder → Task 2. ✅
- Unify vault defaults across onepassword/credential_manager/cli → Task 3. ✅
- `.env.example` tracked, `.env` git-ignored with `DEFAULT_VAULT=Employee` → Task 4. ✅
- CLI `--vault` stays as explicit override → Task 3 (flag retained). ✅
- Tests for loader, resolution, get_profile_mapping, vault default → Tasks 1-3. ✅
- Docs → Task 5. ✅

**Placeholder scan:** No TBD/TODO; every code step shows full code. ✅

**Type consistency:** `_load_dotenv(path, environ)`, `_resolve_mapping_path(filename, project_root)`, `get_mapping_path()`, `get_profile_mapping(profile_name)`, and the `DEFAULT_VAULT` / `PROFILE_MAPPING_FILE` / `PROJECT_ROOT` names are used identically across all tasks. ✅
