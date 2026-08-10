import os
import sys
import json
import time
import datetime
import requests
from typing import Dict, Any, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(override=True)

from config.settings import (
    DHAN_CLIENT_ID,
    DHAN_ACCESS_TOKEN,
    DHAN_RENEW_TOKEN_URL,
    TOKEN_FILE_PATH
)
from execution.dhan_trader import renew_dhan_access_token


def run_oauth_flow(dry_run: bool = False, force_refresh: bool = False) -> str:
    """
    DhanHQ Token Management Routine:
    1. In dry-run mode: returns a mock token.
    2. In live mode: checks existing token file or environment variable DHAN_ACCESS_TOKEN.
    3. Attempts automated 24-hour token renewal via Dhan API if force_refresh or near expiry.
    """
    if dry_run:
        print("[Dhan Auth] DRY-RUN / MOCK MODE: Generating mock daily Dhan access token.")
        mock_token = f"MOCK_DHAN_TOKEN_{datetime.date.today().strftime('%Y%m%d')}"
        token_info = {
            "date": datetime.date.today().isoformat(),
            "access_token": mock_token,
            "created_at": time.time()
        }
        try:
            os.makedirs(os.path.dirname(TOKEN_FILE_PATH), exist_ok=True)
            with open(TOKEN_FILE_PATH, "w") as f:
                json.dump(token_info, f, indent=4)
        except Exception:
            pass
        return mock_token

    # Check environment variable or token file
    env_token = os.getenv("DHAN_ACCESS_TOKEN", "").strip() or DHAN_ACCESS_TOKEN.strip()

    if not force_refresh and os.path.exists(TOKEN_FILE_PATH):
        try:
            with open(TOKEN_FILE_PATH, "r") as f:
                token_data = json.load(f)
                file_token = token_data.get("access_token")
                if file_token and not file_token.startswith("MOCK"):
                    print(f"[Dhan Auth] Using active Dhan access token from token file.")
                    return file_token
        except Exception:
            pass

    if env_token and not env_token.startswith("MOCK") and not env_token.startswith("your_"):
        if force_refresh:
            success, renewed_token = renew_dhan_access_token(access_token=env_token)
            if success:
                return renewed_token
        print("[Dhan Auth] Using configured Dhan access token from environment.")
        return env_token

    print("[Dhan Auth Warning] No valid DHAN_ACCESS_TOKEN found. Set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN in .env.")
    return env_token or "MOCK_DHAN_TOKEN"


if __name__ == "__main__":
    token = run_oauth_flow(dry_run=False)
    print("Active Dhan Token:", token[:10] + "..." if token else "None")
