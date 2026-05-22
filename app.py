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
SHEET_TAB = os.environ.get("SHEET_TAB", "database")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash-lite")

def load_sheet_data():
    try:
        scope = ["https://www.googleapis.com/auth/spreadsheets",
                 "https://www.googleapis.com/auth/drive"]
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SHEET_ID).worksheet(SHEET_TAB)
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        
        # 1. กำแพงกั้นความลับ: ลบคอลัมน์ต้นทุน/กำไรทิ้งทันที
        columns_to_drop = ['ทุน/เส้น', 'ทุน/4 เส้น', 'กำไร']
        df = df.drop(columns=[col for col in columns_to_drop if col in df.columns], errors='ignore')
        
        return df
    except Exception as e:
        print("load error:", repr(e))
        return pd.DataFrame()

def get_shop_data(user_message):
    df = load_sheet_data()
    if df.empty:
        return "ฐานข้อมูลยังไม่พร้อม"
    
    # แปลงคอลัมน์ 'ราคาส่ง' ให้เป็นตัวเลขเพื่อการเรียงลำดับ
    if 'ราคาส่ง' in df.columns:
         df['ราคาส่ง'] = pd.to_numeric(df['ราคาส่ง'].astype(str).str.replace(',', '').str.strip(), errors='coerce').fillna(0)

    # 2. จัดการเรื่องชื่อแบรนด์ (Mapping ภาษาไทยเป็นอังกฤษ)
    brand_map = {
        "โอตานิ": "OTANI", "จีที": "GITI", "เลนโซ่": "LENSO", 
        "โยโก": "YOKOHAMA", "โตโย": "TOYO", "เน็กเซ็น": "NEXEN",
        "อาริซัน": "ARISUN", "เซนทูรี่": "SENTURY"
    }
    search_message = user_message.lower()
    for th, en in brand_map.items():
        if th in user_message:
            search_message += f" {en.lower()}"
    
    df['search_text'] = df['แบรนด์'].astype(str) + " " + df['ขนาดยาง'].astype(str) + " " + df['รุ่นยาง'].astype(str)
    
    keywords = [k for k in search_message.replace("/", " ").replace("-", " ").split() if len(k) > 2]
    
    mask = pd.Series([False]*len(df))
    for kw in keywords:
        mask = mask | df['search_text'].str.lower().str.contains(kw, na=False)
    
    # กรองข้อมูลที่ตรงกัน
    matched_df = df[mask] if mask.any() else df.head(8)
    
    # 3. จัดเรียงราคาจากถูกไปแพง
    if 'ราคาส่ง' in matched_df.columns:
        matched_df = matched_df.sort_values(by='ราคาส่ง', ascending=True)
        
    results = matched_df.head(15)
    
    # สร้างข้อความสำหรับส่งให้ Gemini โดยแสดงเฉพาะข้อมูลที่ปลอดภัย
    text = ""
    for _, r in results.iterrows():
        price_str = f"{int(r['ราคาส่ง']):,}" if r['ราคาส่ง'] > 0 else "N/A"
        text += f"- {r['แบรนด์']} {r['ขนาดยาง']} รุ่น {r['รุ่นยาง']} ราคาส่ง {price_str} บาท\n"
    
    if not mask.any():
        text = "ตัวอย่างสินค้า:\n" + text
    return text

def ask_gemini(user_message, shop_data):
    # ปรับ Prompt ใหม่ทั้งหมด เป็นแบบแยก 5 สถานการณ์ เพื่อให้ตอบตรงประเด็นและกระชับที่สุด
    prompt = f"""คุณคือ "ผู้เชี่ยวชาญด้านยางรถยนต์และนักขายมืออาชีพ" ของร้าน A Little Wheel
บุคลิก: เป็นผู้ชาย สุภาพ, กระตือรือร้นในการช่วยลูกค้า, มีวาทศิลป์ และมีความเป็นทางการ
**ต้องลงท้ายประโยคด้วยคำว่า "ครับ" หรือ "นะครับ" เสมอ** ห้ามใช้คำลงท้ายของผู้หญิงเด็ดขาด

ข้อมูลจากฐานข้อมูล (Real-time เรียงจากราคาถูกไปแพงแล้ว):
{shop_data}

กติกาและหน้าที่ของคุณ:
คุณต้องวิเคราะห์ประโยคของลูกค้า และเลือกตอบ "เพียง 1 สถานการณ์" จาก 5 สถานการณ์ด้านล่างนี้ ให้ตรงประเด็นที่สุด (ห้ามยัดข้อมูลรวมกัน):

สถานการณ์ที่ 1: ลูกค้าทักทาย, ส่งสติกเกอร์ หรือ ยังไม่แจ้งขนาดยาง
- รูปแบบการตอบ: "สวัสดีครับ ลูกค้าสามารถแจ้งขนาดยางที่ใช้งานอยู่ เพื่อให้ทางเราเช็คราคาให้ได้เลยนะครับ"

สถานการณ์ที่ 2: ลูกค้าถามราคา หรือ พิมพ์ขนาดยาง/รุ่นมา
- กฎ: กรองเฉพาะแบรนด์/รุ่นที่ลูกค้าพิมพ์มา (ถ้ามี) เรียงลำดับจาก "ราคาถูก" ไป "ราคาแพง"
- กฎการเว้นบรรทัด: **ต้องเว้น 1 บรรทัดระหว่างรายการสินค้าเสมอ**
- รูปแบบการตอบ (ตัวอย่าง):
  [ขนาดยาง] [แบรนด์] รุ่น [รุ่นยาง]
  • ราคาส่ง [ราคาส่ง] บาท

  [ขนาดยาง] [แบรนด์] รุ่น [รุ่นยาง]
  • ราคาส่ง [ราคาส่ง] บาท

  ‼ ค่าจัดส่งขึ้นอยู่กับจังหวัดหรือสถานที่จัดส่งนะครับ สามารถส่งที่อยู่เพื่อเช็คค่าจัดส่งได้เลยครับ ‼

สถานการณ์ที่ 3: ลูกค้าถามเรื่อง "ของแถม" (เช่น มีจุ๊บไหม, แถมอะไรบ้าง)
- รูปแบบการตอบ: "สำหรับโปรโมชั่นตอนนี้ ทางร้านมีแถมจุ๊บ Pacific แท้ให้ฟรี 4 ตัวครับผม"

สถานการณ์ที่ 4: ลูกค้าถามเรื่อง "สต๊อก" (เช่น มีของไหม, พร้อมส่งไหม)
- รูปแบบการตอบ: "ขออนุญาตเช็คสต๊อกสินค้าสักครู่นะครับ เนื่องจากเป็นราคาขายส่ง สินค้าบางรุ่นอาจจะหมดไว รบกวนรอแอดมินตรวจสอบและแจ้งกลับนะครับ"

สถานการณ์ที่ 5: ลูกค้าถามเรื่อง "ระยะเวลาจัดส่ง" (เช่น กี่วันถึง, ส่งนานไหม)
- รูปแบบการตอบ: "ระยะเวลาจัดส่งเบื้องต้นจะอยู่ที่ประมาณ 3-7 วันครับ หากสินค้ามีพร้อมอยู่ในคลังของทางร้าน จะใช้เวลาจัดส่งไม่เกิน 3 วันครับ แต่ในกรณีที่ต้องเบิกสินค้าจากทางบริษัทผู้ผลิต อาจจะใช้เวลาดำเนินการรวมจัดส่งประมาณ 3-7 วันครับ ทั้งนี้ขึ้นอยู่กับพื้นที่จัดส่งของลูกค้าด้วยนะครับ"

ลูกค้า: {user_message}
ตอบ:"""
    return model.generate_content(prompt).text

def send_message(recipient_id, text):
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
                    # ป้องกันบอทตอบข้อความของตัวเอง (Echo) ซึ่งเป็นสาเหตุของการตอบเบิ้ล
                    if event["message"].get("is_echo"):
                        continue
                    
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
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",8080)))
