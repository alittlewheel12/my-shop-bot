import os, requests, json
from flask import Flask, request
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd

app = Flask(__name__)

PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "tirebot2026")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SHEET_ID = os.environ.get("SHEET_ID")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS_JSON")
SHEET_TAB = os.environ.get("SHEET_TAB", "database") # <-- ใช้ database ตามของคุณ

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash-lite")

def load_sheet_data():
    try:
        # scope ใหม่ที่ Google แนะนำ
        scope = ["https://www.googleapis.com/auth/spreadsheets",
                 "https://www.googleapis.com/auth/drive"]
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SHEET_ID).worksheet(SHEET_TAB)
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        print(f"Loaded {len(df)} rows from {SHEET_TAB}")
        return df
    except Exception as e:
        print("load error:", repr(e))
        return pd.DataFrame()

def get_shop_data(user_message):
    df = load_sheet_data()
    if df.empty:
        return "ฐานข้อมูลยังไม่พร้อม (เช็ค SHEET_ID / แชร์สิทธิ์ Editor / GOOGLE_CREDS_JSON)"

    # รองรับชื่อคอลัมน์ไทยของคุณ
    for col in ['แบรนด์','ขนาดยาง','รุ่นยาง','ราคาส่ง','ทุน/4 เส้น']:
        if col not in df.columns:
            df[col] = ''

    df['search_text'] = df['แบรนด์'].astype(str) + " " + df['ขนาดยาง'].astype(str) + " " + df['รุ่นยาง'].astype(str)
    message = user_message.lower()
    keywords = [k for k in message.replace("/", " ").split() if len(k) > 2]

    mask = pd.Series([False]*len(df))
    for kw in keywords:
        mask = mask | df['search_text'].str.lower().str.contains(kw, na=False)

    results = df[mask].head(15) if mask.any() else df.head(8)

    text = ""
    for _, r in results.iterrows():
        try:
            price = int(float(str(r['ราคาส่ง']).replace(',','')))
            price4 = int(float(str(r['ทุน/4 เส้น']).replace(',','')))
        except:
            price = r['ราคาส่ง']; price4 = r['ทุน/4 เส้น']
        text += f"- {r['แบรนด์']} {r['ขนาดยาง']} รุ่น {r['รุ่นยาง']} ราคาส่ง {price} บาท (4 เส้น {price4} บาท)\n"

    if not mask.any():
        text = "ตัวอย่างสินค้า:\n" + text
    return text

def ask_gemini(user_message, shop_data):
    prompt = f"""คุณคือพนักงานขายส่งยาง ตอบภาษาไทยกระชับ
ข้อมูลจาก Google Sheet (realtime):
{shop_data}

กติกา: ตอบจากข้อมูลเท่านั้น บอกราคาส่งต่อเส้นและ 4 เส้น ถ้าไม่มีให้แนะนำใกล้เคียง
ลูกค้า: {user_message}
ตอบ:"""
    return model.generate_content(prompt).text

def send_message(recipient_id, text):
    # แก้ URL ที่เคยพิมพ์ซ้ำ
    url = "https://graph.facebook.com/v18.0/me/messages"
    r = requests.post(url, params={"access_token": PAGE_ACCESS_TOKEN},
                  json={"recipient":{"id":recipient_id},"message":{"text":text}})
    print("FB send status:", r.status_code, r.text[:200])

@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.mode")=="subscribe" and request.args.get("hub.verify_token")==VERIFY_TOKEN:
        return request.args.get("hub.challenge"),200
    return "Forbidden",403

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    if data.get("object")=="page":
        for entry in data.get("entry",[]):
            for event in entry.get("messaging",[]):
                if "message" in event:
                    sender = event["sender"]["id"]
                    text = event["message"].get("text","")
                    if text:
                        shop_data = get_shop_data(text)
                        reply = ask_gemini(text, shop_data)
                        send_message(sender, reply)
    return "OK",200

@app.route("/health")
def health(): return "OK",200

if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)))
