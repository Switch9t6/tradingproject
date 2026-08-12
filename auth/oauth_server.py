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
    UPSTOX_API_KEY,
    UPSTOX_API_SECRET,
    UPSTOX_REDIRECT_URI,
    UPSTOX_ACCESS_TOKEN,
    TOKEN_FILE_PATH
)


def run_oauth_flow(dry_run: bool = False, force_refresh: bool = False) -> str:
    """
    Upstox API v2 Token Management Routine:
    1. In dry-run mode: returns a mock token.
    2. In live mode: checks existing token file or environment variable UPSTOX_ACCESS_TOKEN.
    """
    if dry_run:
        print("[Upstox Auth] DRY-RUN / MOCK MODE: Generating mock daily Upstox access token.")
        mock_token = f"MOCK_UPSTOX_TOKEN_{datetime.date.today().strftime('%Y%m%d')}"
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
    env_token = os.getenv("UPSTOX_ACCESS_TOKEN", "").strip() or UPSTOX_ACCESS_TOKEN.strip()

    if not force_refresh and os.path.exists(TOKEN_FILE_PATH):
        try:
            with open(TOKEN_FILE_PATH, "r") as f:
                token_data = json.load(f)
                file_token = token_data.get("access_token")
                if file_token and not file_token.startswith("MOCK") and not file_token.startswith("your_"):
                    print(f"[Upstox Auth] Using active Upstox access token from token file.")
                    return file_token
        except Exception:
            pass

    if env_token and not env_token.startswith("MOCK") and not env_token.startswith("your_"):
        print("[Upstox Auth] Using configured Upstox access token from environment.")
        return env_token

    print("[Upstox Auth Warning] No valid UPSTOX_ACCESS_TOKEN found. Set UPSTOX_API_KEY and UPSTOX_ACCESS_TOKEN in .env.")
    return env_token or "MOCK_UPSTOX_TOKEN"


if __name__ == "__main__":
    token = run_oauth_flow(dry_run=False)
    print("Active Upstox Token:", token[:10] + "..." if token else "None")
