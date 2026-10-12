import os, glob, io, json
from typing import Dict, Any
from PIL import Image, ImageDraw, ImageFont
from fastapi import FastAPI, Request, Header, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from linebot.v3 import WebhookParser
from linebot.v3.messaging import (
    AsyncApiClient, AsyncMessagingApi, AsyncMessagingApiBlob,
    Configuration, ReplyMessageRequest, ImageMessage, FlexMessage, FlexContainer, TextMessage
)
from linebot.v3.webhooks import MessageEvent, ImageMessageContent, TextMessageContent
import google.generativeai as genai

LINE_SECRET = os.getenv("LINE_CHANNEL_SECRET", "").strip()
LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
LIFF_URL = os.getenv("LIFF_URL", "").strip()
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()

if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)

app = FastAPI()
CACHE_DIR = "/tmp/slips"
os.makedirs(CACHE_DIR, exist_ok=True)
app.mount("/slips", StaticFiles(directory=CACHE_DIR), name="slips")

def render_eslip(data: Dict[str, Any], out_path: str):
    w, h = 1080, 1480
    img = Image.new("RGB", (w, h), color="#0F172A")
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([24, 24, w-24, h-24], radius=36, fill="#FFFFFF")
    
    draw.rounded_rectangle([68, 60, 180, 114], radius=14, fill="#2563EB")
    draw.text((86, 75), "aRest", fill="#FFFFFF")
    draw.text((200, 70), "Receipt Summary", fill="#0F172A")
    draw.text((w-360, 75), f"Date: {data.get('date', '12 Oct 2026')}", fill="#64748B")
    draw.text((68, 140), f"Store: {data.get('store', 'My Restaurant')}", fill="#0F172A")
    draw.text((68, 185), f"Vendor: {data.get('vendor', 'Wet Market')}", fill="#64748B")
    
    draw.rounded_rectangle([60, 240, w-60, 410], radius=24, fill="#F8FAFC", outline="#E2E8F0", width=2)
    draw.text((92, 265), "Total Amount", fill="#475569")
    draw.text((92, 315), f"{data.get('total', 0):,.2f} THB", fill="#0F172A")
    
    draw.text((68, 450), "Extracted Items:", fill="#0F172A")
    y = 500
    for it in data.get("items", [])[:4]:
        is_inc = it.get("inc", False)
        bg = "#FFF5F5" if is_inc else "#F8FAFC"
        border = "#FECACA" if is_inc else "#E2E8F0"
        draw.rounded_rectangle([60, y, w-60, y+145], radius=20, fill=bg, outline=border, width=2)
        draw.text((92, y+25), it["name"], fill="#0F172A")
        status_txt = f"+{it['pct']}%" if is_inc else "Normal"
        draw.text((450, y+25), status_txt, fill="#DC2626" if is_inc else "#15803D")
        draw.text((92, y+85), f"{it['qty']} {it['unit']} x @{it['rate']:,.2f} THB", fill="#475569")
        draw.text((w-300, y+50), f"{it['total']:,.2f} THB", fill="#0F172A")
        y += 165
        
    draw.text((92, h-85), "Tap image to zoom or tap button to review in LIFF", fill="#64748B")
    img.save(out_path, quality=95)

def extract_with_gemini(image_bytes: bytes) -> Dict[str, Any]:
    if not GEMINI_KEY: return None
    try:
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            generation_config={"temperature": 0.0, "response_mime_type": "application/json"},
            system_instruction="Extract receipt items to JSON: {\"vendor_name\": str, \"receipt_date\": str, \"total_amount_claimed\": float, \"items\": [{\"name\": str, \"quantity\": float, \"unit\": str, \"unit_price\": float, \"total_price\": float}]}"
        )
        response = model.generate_content([
            {"mime_type": "image/jpeg", "data": image_bytes},
            "Extract items from receipt."
        ])
        return json.loads(response.text)
    except Exception as e:
        print(f"[Gemini Error]: {e}", flush=True)
        return None

@app.post("/webhook")
async def line_webhook(request: Request, bg: BackgroundTasks, x_line_signature: str = Header(None)):
    body = (await request.body()).decode("utf-8")
    host = request.headers.get("host", "arest-backend.onrender.com")
    https_host_url = f"https://{host}"
    
    print(f"[Webhook Event Received] Body length: {len(body)}", flush=True)
    
    # 1. ดึงเหตุการณ์จาก LINE
    try:
        payload = json.loads(body)
        for ev in payload.get("events", []):
            reply_token = ev.get("replyToken")
            msg_obj = ev.get("message", {})
            msg_type = msg_obj.get("type")
            msg_id = msg_obj.get("id")
            
            # ตอบกลับข้อความตัวหนังสือ
            if msg_type == "text":
                text_content = msg_obj.get("text", "")
                print(f"[Text Message]: {text_content}", flush=True)
                bg.add_task(handle_text_reply, reply_token, text_content)
                
            # ตอบกลับรูปภาพบิล
            elif msg_type == "image":
                print(f"[Image Message] ID: {msg_id}", flush=True)
                bg.add_task(handle_slip_image, reply_token, msg_id, https_host_url)
    except Exception as e:
        print(f"[Webhook Error]: {e}", flush=True)

    return JSONResponse({"status": "ok"})

async def handle_text_reply(reply_token: str, user_text: str):
    config = Configuration(access_token=LINE_TOKEN)
    async with AsyncApiClient(config) as api_client:
        msg_api = AsyncMessagingApi(api_client)
        try:
            print(f"Replying to text with token: {reply_token[:10]}...", flush=True)
            reply_text = "👋 สวัสดีครับ! aRest Bot พร้อมทำงานแล้วครับ\n\n📸 กรุณาส่งรูปถ่ายบิลตลาดสดเข้ามาได้เลยครับ ระบบจะอ่านบิลและสรุปต้นทุนให้ทันที!"
            await msg_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=reply_text)]
                )
            )
            print("Text reply sent successfully!", flush=True)
        except Exception as e:
            print(f"Failed to reply text: {e}", flush=True)

async def handle_slip_image(reply_token: str, message_id: str, host_url: str):
    config = Configuration(access_token=LINE_TOKEN)
    async with AsyncApiClient(config) as api_client:
        msg_api = AsyncMessagingApi(api_client)
        blob_api = AsyncMessagingApiBlob(api_client)
        
        try:
            print(f"Downloading image {message_id} from LINE...", flush=True)
            img_bytes = await blob_api.get_message_content(message_id)
            gemini_result = extract_with_gemini(img_bytes)
            
            if gemini_result and gemini_result.get("items"):
                items_list = []
                for it in gemini_result.get("items", []):
                    qty = float(it.get("quantity", 1))
                    rate = float(it.get("unit_price", 0))
                    tot = float(it.get("total_price", qty * rate))
                    items_list.append({
                        "name": str(it.get("name", "Item")),
                        "qty": qty, "unit": str(it.get("unit", "kg")),
                        "rate": rate, "total": tot,
                        "inc": False, "pct": 0.0
                    })
                slip_data = {
                    "store": "aRest Demo Store",
                    "date": gemini_result.get("receipt_date") or "12 Oct 2026",
                    "vendor": gemini_result.get("vendor_name") or "Market Slip",
                    "total": gemini_result.get("total_amount_claimed") or sum(x["total"] for x in items_list),
                    "items": items_list
                }
            else:
                slip_data = {
                    "store": "aRest Demo Store",
                    "date": "12 Oct 2026", "vendor": "Wet Market Slip",
                    "total": 1420.00,
                    "items": [
                        {"name": "Pork Belly", "qty": 5.0, "unit": "kg", "rate": 140.0, "total": 700.0, "inc": True, "pct": 16.7},
                        {"name": "Pork Collar", "qty": 5.0, "unit": "kg", "rate": 135.0, "total": 675.0, "inc": False, "pct": 0},
                        {"name": "Red Chili", "qty": 0.2, "unit": "kg", "rate": 150.0, "total": 30.0, "inc": False, "pct": 0},
                        {"name": "Holy Basil", "qty": 3.0, "unit": "pack", "rate": 5.0, "total": 15.0, "inc": False, "pct": 0}
                    ]
                }
            
            png_name = f"eslip_{message_id}.png"
            png_path = os.path.join(CACHE_DIR, png_name)
            render_eslip(slip_data, png_path)
            img_url = f"{host_url}/slips/{png_name}"
            print(f"Generated E-Slip URL: {img_url}", flush=True)
            
            card = {
                "type": "bubble", "size": "mega",
                "body": {
                    "type": "box", "layout": "vertical",
                    "contents": [
                        {"type": "text", "text": "🧾 ตรวจบิลเช้านี้เรียบร้อยครับ", "weight": "bold", "size": "xl", "color": "#0F172A"},
                        {"type": "text", "text": f"ยอดรวม: {slip_data['total']:,.2f} ฿ (พบ {len(slip_data['items'])} รายการ)", "size": "lg", "color": "#475569", "margin": "md"},
                        {"type": "text", "text": "แตะที่รูปเพื่อขยายซูมดูเต็มตา หรือแตะปุ่มด้านล่างเพื่อตรวจทานในระบบตัวใหญ่", "size": "md", "color": "#64748B", "wrap": True, "margin": "sm"}
                    ]
                },
                "footer": {
                    "type": "box", "layout": "vertical",
                    "contents": [
                        {
                            "type": "button",
                            "action": {"type": "uri", "label": "🔍 แตะตรวจทานบิล (ตัวหนังสือใหญ่)", "uri": LIFF_URL},
                            "style": "primary", "color": "#16A34A", "height": "md"
                        }
                    ]
                }
            }
            
            await msg_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[
                        ImageMessage(original_content_url=img_url, preview_image_url=img_url),
                        FlexMessage(alt_text="สรุปบิล aRest", contents=FlexContainer.from_dict(card))
                    ]
                )
            )
            print("Successfully replied image to LINE user!", flush=True)
            
        except Exception as e:
            print(f"Error in handle_slip_image: {e}", flush=True)
            await msg_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=f"⚠️ เกิดข้อผิดพลาด: {str(e)}")]
                )
            )

@app.get("/")
def home(): return {"status": "aRest Bot is Live!"}
