"""AWS IAM CLI wrapper."""

import csv
import io
import json
import os
import subprocess
import time
from typing import cast


class AWSClient:
    """Thin wrapper around AWS CLI IAM commands."""

    @staticmethod
    def _credentials_environment(credentials: dict[str, str]) -> dict[str, str]:
        """Return an environment that authenticates only with ``credentials``.

        Removing inherited AWS credential variables is important when checking a
        newly-created permanent key: an MFA session must not make that check
        succeed on the new key's behalf.
        """
        environment = os.environ.copy()
        for name in (
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "AWS_SECURITY_TOKEN",
            "AWS_PROFILE",
        ):
            environment.pop(name, None)
        environment.update({
            "AWS_ACCESS_KEY_ID": credentials["AccessKeyId"],
            "AWS_SECRET_ACCESS_KEY": credentials["SecretAccessKey"],
        })
        if credentials.get("SessionToken"):
            environment["AWS_SESSION_TOKEN"] = credentials["SessionToken"]
        return environment

    def get_user(self, profile_name: str) -> dict:
        """Get IAM user info for a profile."""
        result = subprocess.run([
            'aws', 'iam', 'get-user',
            '--profile', profile_name,
            '--output', 'json'
        ], capture_output=True, text=True, check=True)
        return cast(dict, json.loads(result.stdout)['User'])

    def change_password(self, profile_name: str, old_password: str, new_password: str) -> None:
        """Change the calling IAM user's console password.

        Passwords are attached with '=' rather than passed as separate
        arguments. Either password may begin with '-', which the AWS CLI
        argument parser would otherwise read as another option and reject.
        """
        subprocess.run([
            'aws', 'iam', 'change-password',
            '--profile', profile_name,
            f'--old-password={old_password}',
            f'--new-password={new_password}',
        ], check=True, capture_output=True)

    def list_access_keys(self, profile_name: str, username: str) -> list[dict]:
        """List access keys for a user."""
        result = subprocess.run([
            'aws', 'iam', 'list-access-keys',
            '--profile', profile_name,
            '--user-name', username,
            '--output', 'json'
        ], capture_output=True, text=True, check=True)
        return cast(list[dict], json.loads(result.stdout)['AccessKeyMetadata'])

    def get_mfa_session(
        self, profile_name: str, mfa_serial_number: str, mfa_code: str
    ) -> dict[str, str]:
        """Exchange an MFA code for a temporary session for IAM mutations."""
        result = subprocess.run([
            'aws', 'sts', 'get-session-token',
            '--profile', profile_name,
            '--serial-number', mfa_serial_number,
            '--token-code', mfa_code,
            '--duration-seconds', '3600',
            '--output', 'json',
        ], capture_output=True, text=True, check=True)
        return cast(dict[str, str], json.loads(result.stdout)['Credentials'])

    def get_mfa_serial_number(self, profile_name: str, username: str) -> str:
        """Return the sole MFA device ARN configured for an IAM user."""
        result = subprocess.run([
            'aws', 'iam', 'list-mfa-devices',
            '--profile', profile_name,
            '--user-name', username,
            '--output', 'json',
        ], capture_output=True, text=True, check=True)
        devices = json.loads(result.stdout)['MFADevices']
        if len(devices) != 1:
            raise ValueError(
                f"Expected exactly one MFA device for {username}; found {len(devices)}"
            )
        return cast(str, devices[0]['SerialNumber'])

    def create_access_key(
        self, profile_name: str, username: str,
        session_credentials: dict[str, str] | None = None,
    ) -> dict:
        """Create a new access key. Returns the AccessKey dict."""
        command = ['aws', 'iam', 'create-access-key']
        if not session_credentials:
            command.extend(['--profile', profile_name])
        command.extend(['--user-name', username, '--output', 'json'])
        result = subprocess.run(command, capture_output=True, text=True, check=True,
            env=(self._credentials_environment(session_credentials)
                 if session_credentials else None))
        return cast(dict, json.loads(result.stdout)['AccessKey'])

    def delete_access_key(
        self, profile_name: str, username: str, access_key_id: str,
        session_credentials: dict[str, str] | None = None,
    ) -> None:
        """Delete an access key."""
        command = ['aws', 'iam', 'delete-access-key']
        if not session_credentials:
            command.extend(['--profile', profile_name])
        command.extend(['--user-name', username, '--access-key-id', access_key_id])
        subprocess.run(command, capture_output=True, text=True, check=True,
            env=(self._credentials_environment(session_credentials)
                 if session_credentials else None))

    def get_user_with_credentials(self, credentials: dict[str, str]) -> dict:
        """Get the caller using explicit credentials, without any profile."""
        result = subprocess.run([
            'aws', 'iam', 'get-user', '--output', 'json',
        ], capture_output=True, text=True, check=True,
            env=self._credentials_environment(credentials))
        return cast(dict, json.loads(result.stdout)['User'])

    def get_password_last_changed(self, profile_name: str) -> str | None:
        """Return ISO timestamp of when the IAM user's console password was last changed.

        Uses the IAM credential report, which is the only AWS source for this data.
        Matches the report row by the profile's actual IAM username (ARN) to avoid
        returning data for the wrong user in multi-user accounts.
        Returns None if the user has no console password or on any error.
        """
        import base64
        try:
            # Resolve the actual IAM username for this profile
            user = self.get_user(profile_name)
            username = user.get('UserName') or user.get('Arn', '')

            # Trigger report generation; keep retrying until COMPLETE
            for _ in range(10):
                gen = subprocess.run([
                    'aws', 'iam', 'generate-credential-report',
                    '--profile', profile_name,
                    '--output', 'json'
                ], capture_output=True, text=True, check=True)
                state = json.loads(gen.stdout).get('State', '')
                if state == 'COMPLETE':
                    break
                time.sleep(2)

            result = subprocess.run([
                'aws', 'iam', 'get-credential-report',
                '--profile', profile_name,
                '--output', 'json'
            ], capture_output=True, text=True, check=True)

            report = json.loads(result.stdout)
            csv_content = base64.b64decode(report['Content']).decode('utf-8')

            reader = csv.DictReader(io.StringIO(csv_content))
            for row in reader:
                # Match by username; the 'user' column in the report is the IAM username
                if row.get('user') != username:
                    continue
                changed = row.get('password_last_changed', 'N/A')
                if changed and changed not in ('N/A', 'not_supported', 'no_information'):
                    return changed
                # User found but no valid password_last_changed — stop searching
                return None
        except Exception:  # noqa: S110 - best-effort lookup; fall back to None
            pass
        return None
