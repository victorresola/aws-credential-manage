# Design: Configurable Vault & Profile-Mapping via `.env`

**Date:** 2026-07-24
**Status:** Approved

## Problem

`ConfigManager.get_profile_mapping` hardcodes an absolute path to
`profile_mapping.json` (`/home/victor/code/.../profile_mapping.json`), so the
tool only works on one machine and one checkout location. It also contains
awkward/dead code (checks the containing folder rather than the file, and has
unreachable statements after `return`).

Separately, the default 1Password vault name and the profile-mapping filename
are not configurable per machine. The built-in vault default is also
inconsistent across the codebase:

- `utils/config.py` → `DEFAULT_VAULT = "Employee"`
- `integrations/onepassword.py` → default `"AWS"`
- `core/credential_manager.py` → default `"Employee"`
- `cli/main.py` `--vault` → default `"Employee"`
- `CLAUDE.md` / `README.md` → document `"AWS"`

## Goals

1. Remove the hardcoded absolute path; resolve the mapping file portably.
2. Make the default vault name and mapping filename configurable via a config
   layer that adapts to each machine / 1Password setup.
3. Support a `.env` file for easy per-machine configuration.
4. Preserve the tool's **zero runtime dependencies** principle.

## Non-Goals

- Changing the profile-mapping JSON schema.
- Changing the CLI surface (the `--vault` flag stays).
- Adding a general-purpose settings framework.

## Design

### 1. Tiny stdlib `.env` loader

A small helper in `utils/config.py` (no third-party dependency):

- `_load_dotenv(path)` reads a `.env` file at the **project root**.
- Parses `KEY=VALUE` lines; ignores blank lines and lines beginning with `#`;
  strips surrounding single/double quotes from values.
- Sets a key in `os.environ` **only if not already present**, so real shell
  environment variables always take precedence over the file.
- Malformed lines are skipped silently (best-effort; never crashes the tool).
- Called once at module import time.

### 2. Config resolution & precedence

Precedence for every configurable value: **real env var > `.env` file >
built-in default**.

- **Project root** is derived from `__file__`:
  `Path(__file__).resolve().parents[2]` (config.py lives at
  `aws_credential_manager/utils/config.py`). Never hardcoded.
- **`DEFAULT_VAULT`**
  - Built-in default: `"AWS"` (matches docs).
  - Env key: `DEFAULT_VAULT`.
- **`PROFILE_MAPPING_FILE`**
  - Built-in default: `"profile_mapping.json"`.
  - Env key: `PROFILE_MAPPING_FILE`.
  - Resolution: if the value is an **absolute** path, use as-is; if
    **relative**, resolve against the project root.

`.env` on this machine will set `DEFAULT_VAULT=Employee`.

### 3. Rewrite `get_profile_mapping`

```
def get_profile_mapping(self, profile_name):
    mapping_path = <resolved PROFILE_MAPPING_FILE>
    if not os.path.exists(mapping_path):
        print warning; return None
    try:
        load JSON
    except JSONDecodeError:
        print warning; return None
    return data.get("profile_mappings", {}).get(profile_name)
```

- Removes the hardcoded path and the unreachable code after `return`.
- Checks the **file** exists (not its folder).
- Same graceful `None`-on-error behavior as today.

### 4. Unify vault defaults

Point the following at the shared `DEFAULT_VAULT` constant instead of their own
string literals, so `.env` controls all of them consistently:

- `integrations/onepassword.py`
- `core/credential_manager.py`
- `cli/main.py` (`--vault` default)

The CLI `--vault` flag remains an explicit per-invocation override layered on
top of the resolved default.

### 5. Supporting files

- Add `.env.example` (tracked) documenting `DEFAULT_VAULT` and
  `PROFILE_MAPPING_FILE`.
- Create this machine's `.env` with `DEFAULT_VAULT=Employee` (git-ignored).
- Ensure `.gitignore` ignores `.env` but keeps `.env.example` tracked.

## Testing

- `.env` loader: parses KEY=VALUE, skips comments/blank lines, strips quotes,
  does not override an already-set env var, tolerates malformed lines.
- Path resolution: relative filename resolves under project root; absolute path
  honored as-is.
- `get_profile_mapping`: returns mapping for a known profile, `None` for an
  unknown profile, `None` + warning when the file is missing, `None` + warning
  on invalid JSON.
- Vault default: falls back to `"AWS"` with no env; reflects env override.

## Risks / Notes

- Zero-dependency principle preserved (loader is stdlib-only).
- Import-time side effect (loading `.env` / reading env) is bounded and
  test-friendly since resolution reads `os.environ` at call time.
