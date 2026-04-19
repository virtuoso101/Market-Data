"""
CSV Exporter
Reads all tabs from a Google Sheet and writes them as CSV files to a
Google Drive folder. Runs as a GitHub Actions step after indicator calculation.

The Drive folder must be shared with the same service account used for Sheets.
"""

import os
import io
import json
import csv

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME", "EOD Market Data")
DRIVE_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID", "")

# Tabs to export — can be overridden via EXPORT_TABS env var (comma-separated)
_default_tabs = ["Assets", "Daily", "Indicators", "Signals"]
_custom_tabs = os.environ.get("EXPORT_TABS")
TABS_TO_EXPORT = [t.strip() for t in _custom_tabs.split(",")] if _custom_tabs else _default_tabs


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def get_credentials():
    """Build credentials for both Sheets and Drive APIs."""
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json:
        raise EnvironmentError("GOOGLE_CREDENTIALS environment variable not set.")
    creds_dict = json.loads(creds_json)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive",
    ]
    return Credentials.from_service_account_info(creds_dict, scopes=scopes)


# ---------------------------------------------------------------------------
# Export Logic
# ---------------------------------------------------------------------------

def sheet_to_csv_bytes(worksheet):
    """Convert a gspread worksheet to CSV bytes."""
    all_values = worksheet.get_all_values()
    output = io.StringIO()
    writer = csv.writer(output)
    for row in all_values:
        writer.writerow(row)
    return output.getvalue().encode("utf-8")


def upload_to_drive(drive_service, folder_id, filename, csv_bytes):
    """Upload (or overwrite) a CSV file in a Google Drive folder.

    If a file with the same name already exists in the folder, it is updated
    in place. Otherwise a new file is created.
    """
    # Check if the file already exists
    query = f"name = '{filename}' and '{folder_id}' in parents and trashed = false"
    results = drive_service.files().list(q=query, fields="files(id)").execute()
    existing = results.get("files", [])

    media = MediaIoBaseUpload(
        io.BytesIO(csv_bytes), mimetype="text/csv", resumable=False
    )

    if existing:
        # Update existing file
        file_id = existing[0]["id"]
        drive_service.files().update(
            fileId=file_id, media_body=media
        ).execute()
        return file_id, "updated"
    else:
        # Create new file
        metadata = {
            "name": filename,
            "parents": [folder_id],
            "mimeType": "text/csv",
        }
        created = drive_service.files().create(
            body=metadata, media_body=media, fields="id"
        ).execute()
        return created["id"], "created"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    from datetime import datetime

    print(f"CSV Exporter - {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print()

    if not DRIVE_FOLDER_ID:
        print("DRIVE_FOLDER_ID not set — skipping CSV export.")
        return

    print("Authenticating...")
    creds = get_credentials()
    gc = gspread.authorize(creds)
    drive_service = build("drive", "v3", credentials=creds)

    print(f"Opening spreadsheet: {SPREADSHEET_NAME}")
    spreadsheet = gc.open(SPREADSHEET_NAME)

    # Derive a filename prefix from the spreadsheet name
    # "EOD Market Data" -> "eod_market_data"
    prefix = SPREADSHEET_NAME.lower().replace(" ", "_")

    for tab_name in TABS_TO_EXPORT:
        try:
            ws = spreadsheet.worksheet(tab_name)
            csv_bytes = sheet_to_csv_bytes(ws)
            filename = f"{prefix}_{tab_name.lower()}.csv"

            file_id, action = upload_to_drive(
                drive_service, DRIVE_FOLDER_ID, filename, csv_bytes
            )
            size_kb = len(csv_bytes) / 1024
            print(f"  ✓ {tab_name} → {filename} ({size_kb:.1f} KB, {action})")

        except gspread.exceptions.WorksheetNotFound:
            print(f"  ⚠ Tab '{tab_name}' not found — skipping")
        except Exception as e:
            print(f"  ✗ {tab_name}: Error — {e}")

    print(f"\n✓ CSV export complete → Drive folder {DRIVE_FOLDER_ID}")


if __name__ == "__main__":
    main()
