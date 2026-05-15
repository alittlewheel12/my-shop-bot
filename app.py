# แทนที่ฟังก์ชัน load_sheet_data เดิมด้วยอันนี้
def load_sheet_data():
    try:
        import google.auth
        scope = ["https://www.googleapis.com/auth/spreadsheets",
                 "https://www.googleapis.com/auth/drive"]
        # ถ้ามี JSON ใช้ JSON, ถ้าไม่มีใช้ service account ของ Cloud Run
        if GOOGLE_CREDS_JSON:
            creds_dict = json.loads(GOOGLE_CREDS_JSON)
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        else:
            creds, _ = google.auth.default(scopes=scope)
        
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SHEET_ID).worksheet(SHEET_TAB)
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        print(f"Loaded {len(df)} rows from {SHEET_TAB}")  # จะเห็นใน Logs
        return df
    except Exception as e:
        print("load error:", repr(e))
        return pd.DataFrame()
