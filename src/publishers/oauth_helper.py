"""OAuth 2.0 helper to authenticate with Google Blogger API and generate tokens."""

import json
import os
from pathlib import Path
from typing import Optional
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from config.settings import settings
from src.utils.logger import console, logger

# Scopes required for full Blogger management (Draft & Publish)
BLOGGER_SCOPES = ["https://www.googleapis.com/auth/blogger"]


def get_blogger_credentials(
    client_secret_path: Optional[Path] = None,
    token_path: Optional[Path] = None,
) -> Credentials:
    """
    Loads valid Google Blogger OAuth credentials.
    1. Checks token_path / token.json
    2. Refreshes if expired
    3. If not valid, runs local OAuth flow using client_secret.json
    """
    secret_file = client_secret_path or settings.get_client_secret_path()
    token_file = token_path or settings.get_token_path()

    creds: Optional[Credentials] = None

    # 1. Load existing token if available
    if token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_file), BLOGGER_SCOPES)
        except Exception as e:
            logger.warning(f"Could not load credentials from {token_file}: {e}")

    # 2. Check environment variable BLOGGER_REFRESH_TOKEN (useful in GitHub Actions / CI)
    refresh_token_env = os.getenv("BLOGGER_REFRESH_TOKEN")
    client_id_env = os.getenv("BLOGGER_CLIENT_ID")
    client_secret_env = os.getenv("BLOGGER_CLIENT_SECRET")

    if not creds and refresh_token_env and client_id_env and client_secret_env:
        creds = Credentials(
            None,
            refresh_token=refresh_token_env,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id_env,
            client_secret=client_secret_env,
            scopes=BLOGGER_SCOPES,
        )

    # 3. Refresh expired token
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            # Save refreshed token
            with open(token_file, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
            logger.info("✅ Blogger OAuth token successfully refreshed.")
            return creds
        except Exception as e:
            logger.warning(f"Failed to refresh token: {e}")
            creds = None

    # 4. If credentials valid, return
    if creds and creds.valid:
        return creds

    # 5. Run interactive local flow if client_secret.json exists
    if not secret_file.exists():
        raise FileNotFoundError(
            f"Blogger client secret file not found at: {secret_file}.\n"
            f"Please download your OAuth 2.0 Client Secret from Google Cloud Console "
            f"and place it as 'client_secret.json' in the project root."
        )

    logger.info("🔑 Initiating Google OAuth authorization flow...")
    flow = InstalledAppFlow.from_client_secrets_file(str(secret_file), BLOGGER_SCOPES)
    creds = flow.run_local_server(port=0)

    # Save token
    with open(token_file, "w", encoding="utf-8") as f:
        f.write(creds.to_json())

    logger.info(f"✅ Authorization successful! Token saved to {token_file}")
    return creds


def authenticate_blogger_oauth():
    """Interactive CLI runner for OAuth setup."""
    console.print("\n[bold cyan]Google Blogger API OAuth Setup[/bold cyan]")
    secret_path = settings.get_client_secret_path()

    if not secret_path.exists():
        console.print(
            f"[bold red]❌ Error:[/bold red] Could not find client secret file at [yellow]{secret_path}[/yellow]\n"
            "Please follow these steps:\n"
            "1. Go to https://console.cloud.google.com/\n"
            "2. Create a project and enable 'Blogger API v3'\n"
            "3. Create OAuth 2.0 Credentials (Application type: Desktop App)\n"
            "4. Download JSON and save as [bold]client_secret.json[/bold] in this folder.\n"
        )
        return False

    try:
        creds = get_blogger_credentials()
        console.print("[bold green]🎉 Blogger API authentication completed successfully![/bold green]")
        if creds.refresh_token:
            console.print(f"[dim]Refresh Token: {creds.refresh_token[:10]}... (Stored in token.json)[/dim]")
        return True
    except Exception as e:
        console.print(f"[bold red]Authentication failed:[/bold red] {e}")
        return False
