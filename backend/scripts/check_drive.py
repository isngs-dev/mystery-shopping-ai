"""Check the Google Drive service-account connection (read-only). Prints what the app would import; changes nothing.

    python scripts/check_drive.py                # uses GOOGLE_SERVICE_ACCOUNT_FILE + GOOGLE_DRIVE_FOLDER_ID from backend/.env
    python scripts/check_drive.py <folder_id>
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.services.drive_import import parse_pdf_name  # noqa: E402
from app.services.drive_sources import GoogleDriveSource, SourceError, drive_status  # noqa: E402

st = drive_status()
print("Service account :", st["service_account_email"] or "NOT CONFIGURED")
if not st["configured"]:
    print("\n" + (st["help"] or ""))
    sys.exit(2)
folder = (sys.argv[1] if len(sys.argv) > 1 else get_settings().google_drive_folder_id) or ""
if not folder:
    print("\nNo folder id. Pass it as an argument or set GOOGLE_DRIVE_FOLDER_ID in backend/.env.")
    sys.exit(2)
try:
    src = GoogleDriveSource(folder)
    root = src.root()
    print(f"Drive folder    : {root.name}  ({folder})")
    kinds: Counter = Counter()
    for client in (n for n in src.children(root) if n.is_dir):
        print(f"\n  {client.name}/")
        for proj in (n for n in src.children(client) if n.is_dir):
            files = [n for n in src.children(proj) if not n.is_dir and n.name.lower().endswith(".pdf")]
            c = Counter((parse_pdf_name(f.name) or (None, 0))[1] for f in files)
            kinds.update(c)
            print(f"    {proj.name:<32} {len(files):>4} PDFs   RAW(_1)={c[1]}  edited(_2)={c[2]}  other={sum(v for k, v in c.items() if k not in (1, 2))}")
    print(f"\nTotals: RAW(_1)={kinds[1]}  edited(_2)={kinds[2]}  other/unnamed={sum(v for k, v in kinds.items() if k not in (1, 2))}")
    print("OK - the service account can read the folder. Turn on Automatic sync in the app.")
except SourceError as exc:
    print("\nFAILED:", exc)
    sys.exit(1)
