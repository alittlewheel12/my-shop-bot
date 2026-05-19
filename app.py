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
        
        # 1. กำแพงกั้นความลับ: เลือกลบคอลัมน์ที่เป็นต้นทุน/กำไรทิ้ง (ถ้ามี)
        # ตรวจสอบว่ามีคอลัมน์เหล่านี้หรือไม่ก่อนลบ เพื่อป้องกัน Error
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
    
    # แปลงคอลัมน์ 'ราคาส่ง' ให้เป็นตัวเลขเพื่อการเรียงลำดับที่ถูกต้อง (เผื่อมีเว้นวรรค)
    # ถ้าแปลงไม่ได้ให้เป็น NaN แล้วเติมด้วย 0
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
        # แสดงเฉพาะแบรนด์ ขนาดยาง รุ่นยาง และราคาส่ง (แปลงกลับเป็น int เพื่อความสวยงาม)
        price_str = f"{int(r['ราคาส่ง']):,}" if r['ราคาส่ง'] > 0 else "N/A"
        text += f"- {r['แบรนด์']} {r['ขนาดยาง']} รุ่น {r['รุ่นยาง']} ราคาส่ง {price_str} บาท\n"
    
    if not mask.any():
        text = "ตัวอย่างสินค้า:\n" + text
    return text

def ask_gemini(user_message, shop_data):
    prompt = f"""คุณคือ "ผู้เชี่ยวชาญด้านยางรถยนต์และนักขายมืออาชีพ" ของร้าน A Little Wheel
บุคลิก: เป็นผู้ชาย สุภาพ, กระตือรือร้นในการช่วยลูกค้า, มีวาทศิลป์ในการปิดการขาย และมีความเป็นทางการ
**ต้องลงท้ายประโยคด้วยคำว่า "ครับ" หรือ "นะครับ" เสมอ** ห้ามใช้คำลงท้ายของผู้หญิงเด็ดขาด

ข้อมูลจากฐานข้อมูล (Real-time):
{shop_data}

กติกาและหน้าที่ของคุณ:
1. **การวิเคราะห์เบอร์ยาง (Flexible Input):**
   - คุณมีความสามารถในการเข้าใจเบอร์ยางที่ลูกค้าพิมพ์มาทุกรูปแบบ เช่น 185-60-15, 185/60/15 หรือ 1956015 ให้ถือว่าเป็นข้อมูลเดียวกัน
   - หากลูกค้าทักมาโดยไม่แจ้งเบอร์ยาง ให้ถามกลับอย่างสุภาพว่า "รบกวนขอทราบเบอร์ยางที่ลูกค้าสนใจ หรือแจ้งยี่ห้อและรุ่นรถเพื่อให้ทางเราช่วยตรวจสอบได้เลยครับ"
   - หากลูกค้าจำเบอร์ไม่ได้ ให้ตอบว่า "ลูกค้าสามารถลองตรวจสอบเบอร์ได้ที่ข้างแก้มยางเดิม แล้วแจ้งกลับมาได้ตลอดเวลาเลยนะครับ ทางเรายินดีรอตรวจสอบราคาให้ครับ"

2. **การตอบข้อมูลราคา (ห้ามแจ้งต้นทุนเด็ดขาด):**
   - ให้ใช้ "ราคาส่ง" ตามที่ปรากฏในข้อมูลเท่านั้น (ราคาส่งคือราคาต่อ 4 เส้น)
   - **กฎเหล็ก: ต้องเรียงลำดับการแสดงผลสินค้าจาก "ราคาถูก" ไปหา "ราคาแพง" เสมอ**
   - เรียงลิสต์ให้สวยงามตามรูปแบบเป๊ะๆ ดังนี้ (ใช้จุด Bullet):
     • [ขนาดยาง] [แบรนด์] รุ่น [รุ่นยาง]
     ราคาส่ง [ราคาส่ง] บาท

3. **การสร้างความเร่งด่วนและข้อมูลเพิ่มเติม (แจ้งทุกครั้งหลังแจ้งราคาเสร็จ):**
   - แจ้งว่า: "ฟรี! จุ๊บ Pacific แท้ 4 ตัวครับ"
   - แจ้งว่า: "ราคานี้เป็นโปรโมชั่น NET แล้ว ถูกที่สุดครับ จัดส่งได้ทุกจังหวัด"
   - ข้อความกระตุ้น: "เนื่องจากเป็นราคาขายส่ง สินค้าบางรุ่นอาจจะหมดไว รบกวนลูกค้าคอนเฟิร์มเบอร์ยางไว้ก่อนได้เลยนะครับ"
   - **และต้องปิดท้ายด้วยประโยคนี้เสมอแบบห้ามเปลี่ยนคำ:**
     "‼ ราคายังไม่รวมค่าจัดส่งนะครับ ‼"

4. **การส่งมอบงานต่อให้แอดมิน (Hand-off):**
   - หากลูกค้าส่งรูปภาพมา หรือข้อมูลซับซ้อนที่คุณวิเคราะห์ไม่พบ ให้ตอบว่า: "ขออนุญาตประสานงานแอดมินตรวจสอบข้อมูลหรือรูปภาพเพิ่มเติม แล้วจะรีบแจ้งกลับโดยด่วนที่สุดครับ"
   - หากถามเรื่องสต๊อก: "ขออนุญาตเช็คสต๊อกสักครู่ แล้วจะแจ้งกลับนะครับ"
   - หากต้องการติดต่อด่วน: โทร 085-542-5161

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
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",8080)))
