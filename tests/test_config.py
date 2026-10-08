"""Tests for ConfigManager."""

import json
from pathlib import Path

import pytest

from aws_credential_manager.utils.config import (
    DEFAULT_VAULT,
    PROJECT_ROOT,
    ConfigManager,
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


class TestConfigManager:
    def test_defaults(self):
        cfg = ConfigManager()
        assert cfg.vault_name == DEFAULT_VAULT
        assert cfg.credentials_path.endswith("/.aws/credentials")

    def test_custom_vault(self):
        cfg = ConfigManager(vault_name="Other")
        assert cfg.vault_name == "Other"

    def test_get_aws_profiles_parses_valid_sections(self, aws_credentials_file: Path):
        cfg = ConfigManager(credentials_path=str(aws_credentials_file))
        profiles = cfg.get_aws_profiles()
        names = {p["name"] for p in profiles}
        assert names == {"profile-one", "profile-two"}

    def test_get_aws_profiles_skips_sections_without_key(
        self, aws_credentials_file: Path
    ):
        cfg = ConfigManager(credentials_path=str(aws_credentials_file))
        names = {p["name"] for p in cfg.get_aws_profiles()}
        assert "no-key-section" not in names

    def test_get_aws_profiles_returns_credentials(self, aws_credentials_file: Path):
        cfg = ConfigManager(credentials_path=str(aws_credentials_file))
        one = next(p for p in cfg.get_aws_profiles() if p["name"] == "profile-one")
        assert one["access_key_id"] == "AKIAONE"
        assert one["secret_access_key"] == "secretone"

    def test_missing_file_raises(self, tmp_path: Path):
        cfg = ConfigManager(credentials_path=str(tmp_path / "nope"))
        with pytest.raises(FileNotFoundError):
            cfg.get_aws_profiles()
