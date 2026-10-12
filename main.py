import os, json, base64, urllib.request, urllib.error
from typing import Dict, Any
from fastapi import FastAPI, Request, Header, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from linebot.v3 import WebhookParser
from linebot.v3.messaging import (
    AsyncApiClient, AsyncMessagingApi, AsyncMessagingApiBlob,
    Configuration, ReplyMessageRequest, FlexMessage, FlexContainer, TextMessage
)

LINE_SECRET = os.getenv("LINE_CHANNEL_SECRET", "").strip()
LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
BASE_LIFF_URL = "https://liff.line.me/2011976661-wOEoqvqy"
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SLIP_CACHE: Dict[str, Any] = {}
LATEST_SLIP: Dict[str, Any] = {}

@app.get("/api/slip/{slip_id}")
def get_slip(slip_id: str):
    if slip_id in SLIP_CACHE:
        return SLIP_CACHE[slip_id]
    if LATEST_SLIP:
        return LATEST_SLIP
    return {"status": "not_found", "items": []}

@app.get("/api/latest-slip")
def get_latest_slip():
    if LATEST_SLIP:
        return LATEST_SLIP
    return {"status": "empty", "items": []}

def get_available_gemini_models() -> list:
    if not GEMINI_KEY:
        return ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash"]
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_KEY}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            active_models = []
            for m in data.get("models", []):
                if "generateContent" in m.get("supportedGenerationMethods", []):
                    active_models.append(m["name"].replace("models/", ""))
            ordered = []
            for pref in ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash", "gemini-1.5-flash"]:
                if pref in active_models:
                    ordered.append(pref)
            for m in active_models:
                if "flash" in m and m not in ordered:
                    ordered.append(m)
            return ordered if ordered else active_models
    except Exception:
        return ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash"]

def extract_receipt_with_gemini(image_bytes: bytes) -> Dict[str, Any]:
    if not GEMINI_KEY:
        return {"error": "ไม่พบคีย์ GEMINI_API_KEY บนเซิร์ฟเวอร์"}
        
    candidate_models = get_available_gemini_models()
    headers = {"Content-Type": "application/json"}
    img_b64 = base64.b64encode(image_bytes).decode("utf-8")
    
    prompt = """
    คุณคือ AI ผู้เชี่ยวชาญการอ่านลายมือบิลและรายการสั่งซื้อของตลาดสดไทย
    หน้าที่: สกัดรายการวัตถุดิบ จำนวน และราคาจากลายมือภาษาไทยทั้งหมดในภาพเป็น JSON:
    {
      "vendor_name": "รายการจดตลาดสด",
      "receipt_date": "12 ต.ค. 2026",
      "items": [
        {
          "name": "ชื่อวัตถุดิบ (เช่น หมูสับ, น้ำปลา, ขิง, ไข่ไก่, คะน้า, พริกสด)",
          "quantity": ตัวเลขจำนวน (ถ้ามี '2 กก.' ให้เป็น 2, '1/2 กก.' ให้เป็น 0.5, '1 ขวด' ให้เป็น 1),
          "unit": "หน่วย (เช่น กก., ขวด, แผง, ถุง, กำ, ลูก)",
          "total_price": ราคารวมที่เขียนไว้ท้ายบรรทัด (เช่น 190, 58, 40, 130, ถ้าไม่มีให้ใส่ 0)
        }
      ]
    }
    ข้อสำคัญ: อ่านลายมือจากภาพจริงทั้งหมด ทั้งฝั่งซ้ายและฝั่งขวา!
    """
    
    payload = {
        "contents": [{"parts": [{"text": prompt}, {"inlineData": {"mimeType": "image/jpeg", "data": img_b64}}]}],
        "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json"}
    }
    
    last_error = None
    for model_name in candidate_models[:4]:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_KEY}"
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
            with urllib.request.urlopen(req, timeout=25) as resp:
                res_json = json.loads(resp.read().decode("utf-8"))
                raw_text = res_json["candidates"][0]["content"]["parts"][0]["text"]
                clean_text = raw_text.strip()
                if clean_text.startswith("```"):
                    clean_text = clean_text.split("\n", 1)[-1]
                if clean_text.endswith("```"):
                    clean_text = clean_text.rsplit("```", 1)[0]
                return json.loads(clean_text.strip())
        except Exception as e:
            last_error = str(e)
            
    return {"error": f"Google Error: {last_error}"}

def build_receipt_flex(slip_data: Dict[str, Any], liff_url: str) -> Dict[str, Any]:
    items = slip_data.get("items", [])
    item_rows = []
    
    for it in items[:10]:
        price_val = float(it.get("total_price", 0.0))
        price_text = f"{price_val:,.2f} ฿" if price_val > 0 else "รอใส่ราคา"
        item_rows.append({
            "type": "box", "layout": "horizontal",
            "contents": [
                {"type": "text", "text": f"• {it['name']} ({it.get('quantity', 1)} {it.get('unit', '')})", "size": "sm", "color": "#0F172A", "weight": "bold", "flex": 4},
                {"type": "text", "text": price_text, "size": "sm", "color": "#64748B" if price_val == 0 else "#1E293B", "align": "end", "weight": "bold", "flex": 2}
            ],
            "margin": "sm"
        })

    if len(items) > 10:
        item_rows.append({
            "type": "text",
            "text": f"... และอีก {len(items) - 10} รายการ (แตะปุ่มเพื่อตรวจทานทั้งหมด)",
            "size": "xs", "color": "#2563EB", "margin": "md", "weight": "bold"
        })

    total_price = sum(float(it.get("total_price", 0)) for it in items)
    hero_text = f"{total_price:,.2f} บาท" if total_price > 0 else f"ตรวจพบ {len(items)} รายการ"

    return {
        "type": "bubble", "size": "mega",
        "body": {
            "type": "box", "layout": "vertical",
            "contents": [
                {"type": "box", "layout": "horizontal", "contents": [
                    {"type": "text", "text": "aRest", "weight": "bold", "color": "#2563EB", "size": "md"},
                    {"type": "text", "text": "ตรวจรายการตลาดสด", "weight": "bold", "size": "xl", "color": "#0F172A", "flex": 3}
                ]},
                {"type": "text", "text": f"สกัดจากภาพถ่ายจริง | วันที่: 12 ต.ค. 2026", "size": "sm", "color": "#64748B", "margin": "sm"},
                {"type": "box", "layout": "vertical", "margin": "lg", "paddingAll": "16px", "backgroundColor": "#F8FAFC", "cornerRadius": "16px",
                 "contents": [
                     {"type": "text", "text": "สรุปข้อมูลที่ AI อ่านได้จากลายมือ", "size": "sm", "color": "#475569"},
                     {"type": "text", "text": hero_text, "size": "xl", "weight": "bold", "color": "#1E3A8A", "margin": "sm"}
                 ]},
                {"type": "text", "text": f"รายการวัตถุดิบ (ทั้งหมด {len(items)} รายการ):", "size": "sm", "color": "#64748B", "margin": "lg", "weight": "bold"},
                {"type": "box", "layout": "vertical", "contents": item_rows}
            ]
        },
        "footer": {
            "type": "box", "layout": "vertical",
            "contents": [{
                "type": "button",
                "action": {"type": "uri", "label": "🔍 ตรวจทาน / ใส่ราคา", "uri": liff_url},
                "style": "primary", "color": "#16A34A", "height": "md"
            }]
        }
    }

@app.post("/webhook")
async def line_webhook(request: Request, bg: BackgroundTasks, x_line_signature: str = Header(None)):
    body = (await request.body()).decode("utf-8")
    try:
        payload = json.loads(body)
        for ev in payload.get("events", []):
            reply_token = ev.get("replyToken")
            msg_obj = ev.get("message", {})
            if reply_token and msg_obj.get("type") == "image":
                bg.add_task(handle_slip_image_reply, reply_token, msg_obj.get("id"))
    except Exception as e:
        print(f"[Webhook Error]: {e}", flush=True)
    return JSONResponse({"status": "ok"})

async def handle_slip_image_reply(reply_token: str, message_id: str):
    global LATEST_SLIP
    config = Configuration(access_token=LINE_TOKEN)
    async with AsyncApiClient(config) as api_client:
        msg_api = AsyncMessagingApi(api_client)
        blob_api = AsyncMessagingApiBlob(api_client)
        try:
            img_bytes = await blob_api.get_message_content(message_id)
            extracted_data = extract_receipt_with_gemini(img_bytes)
            
            # บันทึกข้อมูลบิลทั้งตาม ID และบันทึกเป็นบิลล่าสุด
            SLIP_CACHE[message_id] = extracted_data
            LATEST_SLIP = extracted_data
            
            dynamic_liff_url = f"{BASE_LIFF_URL}?id={message_id}"
            
            flex_content = build_receipt_flex(extracted_data, dynamic_liff_url)
            await msg_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[FlexMessage(alt_text="สรุปรายการตลาดสด aRest", contents=FlexContainer.from_dict(flex_content))]
                )
            )
            print(f"Replied successfully for slip {message_id}!", flush=True)
        except Exception as e:
            print(f"Reply error: {e}", flush=True)

@app.get("/")
def home(): return {"status": "aRest Bot Live!"}
