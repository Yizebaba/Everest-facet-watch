"""Earth Engine authorization helper for this isolated monitor only."""
import sys
import json
from pathlib import Path

import ee
from ee.oauth import Flow


SECRETS = Path("/app/secrets")
VERIFIER = SECRETS / ".earthengine-pkce-verifier"


def start() -> None:
    SECRETS.mkdir(parents=True, exist_ok=True)
    flow = Flow(auth_mode="notebook")
    VERIFIER.write_text(flow.code_verifier, encoding="ascii")
    print("Open this Earth Engine authorization URL in a browser:\n")
    print(flow.auth_url)
    print("\nReturn the generated verification code to complete this isolated authorization.")


def finish(code: str) -> None:
    verifier = VERIFIER.read_text(encoding="ascii").strip()
    ee.Authenticate(authorization_code=code, quiet=True, code_verifier=verifier)
    VERIFIER.unlink(missing_ok=True)
    print("Earth Engine credentials saved for the isolated monitor.")


def repair() -> None:
    """Remove the redundant scope field rejected by google-auth refresh."""
    credentials = Path(ee.oauth.get_credentials_path())
    value = json.loads(credentials.read_text(encoding="utf-8"))
    value.pop("scopes", None)
    temporary = credentials.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=True) + "\n", encoding="utf-8")
    temporary.replace(credentials)
    print("Earth Engine credential compatibility field repaired.")


if __name__ == "__main__":
    if len(sys.argv) == 1 or sys.argv[1] == "start":
        start()
    elif sys.argv[1] == "finish" and len(sys.argv) == 3:
        finish(sys.argv[2])
    elif sys.argv[1] == "repair":
        repair()
    else:
        raise SystemExit("Usage: authorize.py start | authorize.py finish AUTHORIZATION_CODE | authorize.py repair")
