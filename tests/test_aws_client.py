"""Tests for AWSClient IAM wrapper."""

import base64
import json

import pytest

from aws_credential_manager.integrations.aws_client import AWSClient
from tests.conftest import FakeCompletedProcess

MODULE = "aws_credential_manager.integrations.aws_client.subprocess"


@pytest.fixture
def client():
    return AWSClient()


class TestGetUser:
    def test_returns_user_dict(self, client, mocker):
        run = mocker.patch(MODULE).run
        run.return_value = FakeCompletedProcess(
            stdout=json.dumps({"User": {"UserName": "bob"}})
        )
        assert client.get_user("dev") == {"UserName": "bob"}
        args = run.call_args.args[0]
        assert args[:3] == ["aws", "iam", "get-user"]
        assert "--profile" in args and "dev" in args


class TestChangePassword:
    def test_builds_command(self, client, mocker):
        run = mocker.patch(MODULE).run
        run.return_value = FakeCompletedProcess()
        client.change_password("dev", "old-pw", "new-pw")
        args = run.call_args.args[0]
        assert args[:3] == ["aws", "iam", "change-password"]
        assert "--old-password=old-pw" in args
        assert "--new-password=new-pw" in args
        assert "--user-name" not in args

    def test_passwords_starting_with_hyphens_are_not_parsed_as_options(
        self, client, mocker
    ):
        """Bare password values beginning with '-' make the AWS CLI reject them."""
        run = mocker.patch(MODULE).run
        run.return_value = FakeCompletedProcess()
        client.change_password("dev", "-old-pw", "-new-pw")
        args = run.call_args.args[0]
        assert "--old-password=-old-pw" in args
        assert "--new-password=-new-pw" in args
        assert "-old-pw" not in args
        assert "-new-pw" not in args


class TestListAccessKeys:
    def test_returns_metadata_list(self, client, mocker):
        keys = [{"AccessKeyId": "AKIA1"}]
        mocker.patch(MODULE).run.return_value = FakeCompletedProcess(
            stdout=json.dumps({"AccessKeyMetadata": keys})
        )
        assert client.list_access_keys("dev", "bob") == keys


class TestCreateAccessKey:
    def test_returns_access_key(self, client, mocker):
        key = {"AccessKeyId": "AKIANEW", "SecretAccessKey": "shh"}
        mocker.patch(MODULE).run.return_value = FakeCompletedProcess(
            stdout=json.dumps({"AccessKey": key})
        )
        assert client.create_access_key("dev", "bob") == key

    def test_uses_mfa_session_credentials_when_provided(self, client, mocker):
        key = {"AccessKeyId": "AKIANEW", "SecretAccessKey": "shh"}
        run = mocker.patch(MODULE).run
        run.return_value = FakeCompletedProcess(stdout=json.dumps({"AccessKey": key}))

        client.create_access_key(
            "dev", "bob",
            {"AccessKeyId": "ASIASESSION", "SecretAccessKey": "session-secret",
             "SessionToken": "session-token"},
        )

        environment = run.call_args.kwargs["env"]
        assert "--profile" not in run.call_args.args[0]
        assert environment["AWS_ACCESS_KEY_ID"] == "ASIASESSION"
        assert environment["AWS_SECRET_ACCESS_KEY"] == "session-secret"
        assert environment["AWS_SESSION_TOKEN"] == "session-token"


class TestMfaSession:
    def test_requests_an_sts_session_with_mfa(self, client, mocker):
        credentials = {
            "AccessKeyId": "ASIASESSION", "SecretAccessKey": "session-secret",
            "SessionToken": "session-token",
        }
        run = mocker.patch(MODULE).run
        run.return_value = FakeCompletedProcess(
            stdout=json.dumps({"Credentials": credentials})
        )

        assert client.get_mfa_session("dev", "arn:aws:iam::123:mfa/bob", "123456") == credentials
        args = run.call_args.args[0]
        assert args[:4] == ["aws", "sts", "get-session-token", "--profile"]
        assert "arn:aws:iam::123:mfa/bob" in args
        assert "123456" in args

    def test_gets_the_only_configured_mfa_device(self, client, mocker):
        run = mocker.patch(MODULE).run
        run.return_value = FakeCompletedProcess(stdout=json.dumps({
            "MFADevices": [{"SerialNumber": "arn:aws:iam::123:mfa/bob"}],
        }))

        assert client.get_mfa_serial_number("dev", "bob") == "arn:aws:iam::123:mfa/bob"

    def test_rejects_missing_or_ambiguous_mfa_devices(self, client, mocker):
        run = mocker.patch(MODULE).run
        run.return_value = FakeCompletedProcess(stdout=json.dumps({"MFADevices": []}))

        with pytest.raises(ValueError, match="exactly one MFA device"):
            client.get_mfa_serial_number("dev", "bob")


class TestGetUserWithCredentials:
    def test_uses_only_explicit_credentials(self, client, mocker, monkeypatch):
        monkeypatch.setenv("AWS_SESSION_TOKEN", "wrong-session")
        run = mocker.patch(MODULE).run
        run.return_value = FakeCompletedProcess(
            stdout=json.dumps({"User": {"UserName": "bob"}})
        )

        assert client.get_user_with_credentials(
            {"AccessKeyId": "AKIANEW", "SecretAccessKey": "new-secret"}
        ) == {"UserName": "bob"}
        environment = run.call_args.kwargs["env"]
        assert environment["AWS_ACCESS_KEY_ID"] == "AKIANEW"
        assert "AWS_SESSION_TOKEN" not in environment


class TestDeleteAccessKey:
    def test_builds_command(self, client, mocker):
        run = mocker.patch(MODULE).run
        run.return_value = FakeCompletedProcess()
        client.delete_access_key("dev", "bob", "AKIAOLD")
        args = run.call_args.args[0]
        assert args[:3] == ["aws", "iam", "delete-access-key"]
        assert "--access-key-id" in args and "AKIAOLD" in args


class TestGetPasswordLastChanged:
    def _report(self, rows):
        csv_lines = ["user,password_last_changed"]
        csv_lines += [f"{u},{c}" for u, c in rows]
        content = base64.b64encode("\n".join(csv_lines).encode()).decode()
        return content

    def test_returns_timestamp_for_matching_user(self, client, mocker):
        sub = mocker.patch(MODULE)

        def fake_run(cmd, **kwargs):
            if "get-user" in cmd:
                return FakeCompletedProcess(
                    stdout=json.dumps({"User": {"UserName": "bob"}})
                )
            if "generate-credential-report" in cmd:
                return FakeCompletedProcess(
                    stdout=json.dumps({"State": "COMPLETE"})
                )
            if "get-credential-report" in cmd:
                return FakeCompletedProcess(
                    stdout=json.dumps(
                        {"Content": self._report([("bob", "2026-01-01T00:00:00Z")])}
                    )
                )
            return FakeCompletedProcess()

        sub.run.side_effect = fake_run
        assert client.get_password_last_changed("dev") == "2026-01-01T00:00:00Z"

    def test_returns_none_when_no_valid_value(self, client, mocker):
        sub = mocker.patch(MODULE)

        def fake_run(cmd, **kwargs):
            if "get-user" in cmd:
                return FakeCompletedProcess(
                    stdout=json.dumps({"User": {"UserName": "bob"}})
                )
            if "generate-credential-report" in cmd:
                return FakeCompletedProcess(stdout=json.dumps({"State": "COMPLETE"}))
            if "get-credential-report" in cmd:
                return FakeCompletedProcess(
                    stdout=json.dumps({"Content": self._report([("bob", "N/A")])})
                )
            return FakeCompletedProcess()

        sub.run.side_effect = fake_run
        assert client.get_password_last_changed("dev") is None

    def test_returns_none_on_error(self, client, mocker):
        sub = mocker.patch(MODULE)
        sub.run.side_effect = RuntimeError("boom")
        assert client.get_password_last_changed("dev") is None
