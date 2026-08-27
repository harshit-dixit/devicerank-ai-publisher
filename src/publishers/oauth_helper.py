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


def _mask_secret(val: Optional[str], unmask: bool = False) -> str:
    """Masks secret values to prevent terminal/CI leakage unless explicitly unmasked."""
    if not val:
        return "(Not Set)"
    if unmask:
        return val
    cleaned = str(val).strip()
    if len(cleaned) <= 8:
        return "****"
    return f"{cleaned[:4]}...{cleaned[-4:]}"


def get_blogger_credentials(
    client_secret_path: Optional[Path] = None,
    token_path: Optional[Path] = None,
) -> Credentials:
    """Loads valid Google Blogger OAuth credentials.

    1. Checks token_path / token.json
    2. Refreshes if expired
    3. Checks environment variables (GitHub Actions mode)
    4. If not valid, runs local OAuth flow using client_secret.json
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

    # 2. Check environment variables (e.g., in GitHub Actions)
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

    # 3. Refresh an expired *or token-less* credential.
    #
    # Credentials created from GitHub Actions secrets deliberately start with no
    # access token. google-auth considers those credentials invalid, but not
    # necessarily expired because they have no expiry timestamp. Refreshing on
    # ``not valid`` is what exchanges the long-lived refresh token for the
    # short-lived access token required by the Blogger API.
    if creds and creds.refresh_token and not creds.valid:
        try:
            creds.refresh(Request())
            # Save refreshed token if writing is permitted
            try:
                with open(token_file, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())
            except Exception:
                pass
            logger.info("Blogger OAuth token successfully refreshed.")
            return creds
        except Exception as e:
            logger.warning(f"Failed to refresh token: {e}")
            creds = None

    # 4. If credentials valid, return
    if creds and creds.valid:
        return creds

    # 5. Run interactive local flow if client_secret.json exists
    if not secret_file.exists():
        if os.getenv("GITHUB_ACTIONS") == "true":
            raise RuntimeError(
                "Blogger OAuth credentials are unavailable in GitHub Actions. "
                "Configure BLOGGER_CLIENT_ID, BLOGGER_CLIENT_SECRET, and "
                "BLOGGER_REFRESH_TOKEN as repository secrets."
            )
        raise FileNotFoundError(
            f"Blogger client secret file not found at: {secret_file}.\n"
            f"Please download your OAuth 2.0 Client Secret from Google Cloud Console "
            f"and place it as 'client_secret.json' in the project root."
        )

    logger.info("Initiating Google OAuth authorization flow...")
    flow = InstalledAppFlow.from_client_secrets_file(str(secret_file), BLOGGER_SCOPES)
    creds = flow.run_local_server(port=0)

    # Save token
    with open(token_file, "w", encoding="utf-8") as f:
        f.write(creds.to_json())

    logger.info(f"Authorization successful! Token saved to {token_file}")
    return creds


def authenticate_blogger_oauth() -> bool:
    """Interactive CLI runner for OAuth setup."""
    console.print("\n[bold cyan]Google Blogger API OAuth Setup[/bold cyan]")
    secret_path = settings.get_client_secret_path()

    if not secret_path.exists():
        console.print(
            f"[bold red]Error:[/bold red] Could not find client secret file at [yellow]{secret_path}[/yellow]\n"
            "Please follow these steps:\n"
            "1. Go to https://console.cloud.google.com/\n"
            "2. Create a project and enable 'Blogger API v3'\n"
            "3. Create OAuth 2.0 Credentials (Application type: Desktop App)\n"
            "4. Download JSON and save as [bold]client_secret.json[/bold] in this folder.\n"
        )
        return False

    try:
        creds = get_blogger_credentials()
        console.print("[bold green]Blogger API authentication completed successfully![/bold green]")
        if creds.refresh_token:
            console.print("[dim]Refresh Token generated and stored in token.json[/dim]")
        return True
    except Exception as e:
        console.print(f"[bold red]Authentication failed:[/bold red] {e}")
        return False


def export_github_secrets_info(unmask: bool = False):
    """Helper to display values for configuring GitHub Repository Secrets (masked by default for security)."""
    console.print("\n[bold cyan]GitHub Repository Secrets Helper[/bold cyan]")
    console.print("Add the following Secrets under: [yellow]GitHub Repo -> Settings -> Secrets and variables -> Actions[/yellow]\n")

    secret_file = settings.get_client_secret_path()
    token_file = settings.get_token_path()

    client_id = ""
    client_secret = ""
    refresh_token = ""

    if secret_file.exists():
        try:
            with open(secret_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                installed = data.get("installed", data.get("web", {}))
                client_id = installed.get("client_id", "")
                client_secret = installed.get("client_secret", "")
        except Exception:
            pass

    if token_file.exists():
        try:
            with open(token_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                refresh_token = data.get("refresh_token", "")
        except Exception:
            pass

    gemini_key = settings.gemini_api_key or os.getenv("GEMINI_API_KEY")
    blog_id = settings.blogger_blog_id or os.getenv("BLOGGER_BLOG_ID")

    console.print(f"[bold]GEMINI_API_KEY:[/bold] {_mask_secret(gemini_key, unmask)}")
    console.print(f"[bold]BLOGGER_BLOG_ID:[/bold] {blog_id or '(Set in .env)'}")
    console.print(f"[bold]BLOGGER_CLIENT_ID:[/bold] {_mask_secret(client_id, unmask)}")
    console.print(f"[bold]BLOGGER_CLIENT_SECRET:[/bold] {_mask_secret(client_secret, unmask)}")
    console.print(f"[bold]BLOGGER_REFRESH_TOKEN:[/bold] {_mask_secret(refresh_token, unmask)}")

    if not unmask:
        console.print("\n[dim]Note: Sensitive secrets are masked. Use `python -m src.main export-secrets --unmask` to reveal full values locally.[/dim]")
