import os, json, base64, urllib.request
from typing import Dict, Any
from fastapi import FastAPI, Request, Header, BackgroundTasks
from fastapi.responses import JSONResponse

from linebot.v3 import WebhookParser
from linebot.v3.messaging import (
    AsyncApiClient, AsyncMessagingApi, AsyncMessagingApiBlob,
    Configuration, ReplyMessageRequest, FlexMessage, FlexContainer, TextMessage
)

LINE_SECRET = os.getenv("LINE_CHANNEL_SECRET", "").strip()
LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
LIFF_URL = "https://liff.line.me/2011976661-wOEoqvqy"
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()

app = FastAPI()

def extract_receipt_with_gemini(image_bytes: bytes) -> Dict[str, Any]:
    """เรียก Gemini 1.5 Flash ผ่าน REST API สกัดลายมือภาษาไทยสดๆ 100% (ไม่มี Mock)"""
    if not GEMINI_KEY:
        print("[Error]: GEMINI_API_KEY is not set", flush=True)
        return {"error": "ไม่พบคีย์ GEMINI_API_KEY บนเซิร์ฟเวอร์"}
        
    try:
        print("[Gemini Dynamic]: Processing real image with Gemini 1.5 Flash...", flush=True)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}"
        headers = {"Content-Type": "application/json"}
        img_b64 = base64.b64encode(image_bytes).decode("utf-8")
        
        prompt = """
        คุณคือ AI ผู้เชี่ยวชาญการอ่านลายมือบิลและรายการสั่งซื้อของตลาดสดไทยสำหรับระบบ aRest
        หน้าที่: สกัดรายการวัตถุดิบและจำนวนจากลายมือภาษาไทยในภาพทั้งหมด ออกมาเป็น JSON:
        {
          "vendor_name": "ชื่อร้านหรือหัวกระดาษ (ถ้ามี)",
          "receipt_date": "วันที่ (ถ้ามี)",
          "items": [
            {
              "name": "ชื่อวัตถุดิบที่เขียนในภาพ (เช่น กุ้งสด, หมูบด, ผักคะน้า, เส้นเล็ก)",
              "quantity": ตัวเลขจำนวน (ถ้ามี '2 ล' ให้เป็น 2, 'ครึ่งโล' ให้เป็น 0.5, ถ้าไม่ระบุให้เป็น 1),
              "unit": "หน่วย (เช่น กก., โล, ขีด, ถุง, กำ, อัน, ห่อ, ฟอง, แผง)",
              "total_price": ราคารวม (ถ้าไม่มีเขียนราคาไว้ ให้ใส่ 0)
            }
          ]
        }
        ข้อสำคัญ: อ่านลายมือภาษาไทยสดๆ จากภาพนี้เท่านั้น ห้ามสร้างข้อมูลขึ้นเองเด็ดขาด
        """
        
        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {
                        "inlineData": {
                            "mimeType": "image/jpeg",
                            "data": img_b64
                        }
                    }
                ]
            }],
            "generationConfig": {
                "temperature": 0.0,
                "responseMimeType": "application/json"
            }
        }
        
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
        with urllib.request.urlopen(req, timeout=25) as resp:
            res_json = json.loads(resp.read().decode("utf-8"))
            raw_text = res_json["candidates"][0]["content"]["parts"][0]["text"]
            
            # ลบ markdown formatting ถ้ามี
            clean_text = raw_text.strip()
            if clean_text.startswith("```"):
                clean_text = clean_text.split("\n", 1)[-1]
            if clean_text.endswith("```"):
                clean_text = clean_text.rsplit("```", 1)[0]
            clean_text = clean_text.strip()
            
            parsed = json.loads(clean_text)
            print(f"[Gemini Real Success]: Extracted {len(parsed.get('items', []))} items dynamically!", flush=True)
            return parsed
            
    except Exception as e:
        print(f"[Gemini Error]: {e}", flush=True)
        return {"error": f"Gemini Error: {str(e)}"}

def build_receipt_flex(slip_data: Dict[str, Any]) -> Dict[str, Any]:
    items = slip_data.get("items", [])
    item_rows = []
    
    # แสดงรายการจริงที่สกัดได้แบบ Dynamic
    for it in items[:10]:
        price_val = float(it.get("total_price", 0.0))
        price_text = f"{price_val:,.2f} ฿" if price_val > 0 else "รอใส่ราคา"
        item_rows.append({
            "type": "box",
            "layout": "horizontal",
            "contents": [
                {
                    "type": "text",
                    "text": f"• {it['name']} ({it.get('quantity', 1)} {it.get('unit', '')})",
                    "size": "sm",
                    "color": "#0F172A",
                    "weight": "bold",
                    "flex": 4
                },
                {
                    "type": "text",
                    "text": price_text,
                    "size": "sm",
                    "color": "#64748B" if price_val == 0 else "#1E293B",
                    "align": "end",
                    "weight": "bold",
                    "flex": 2
                }
            ],
            "margin": "sm"
        })

    if len(items) > 10:
        item_rows.append({
            "type": "text",
            "text": f"... และอีก {len(items) - 10} รายการ (แตะปุ่มเพื่อดูทั้งหมด)",
            "size": "xs",
            "color": "#2563EB",
            "margin": "md",
            "weight": "bold"
        })

    total_price = sum(float(it.get("total_price", 0)) for it in items)
    hero_text = f"{total_price:,.2f} บาท" if total_price > 0 else f"ตรวจพบ {len(items)} รายการ"

    bubble = {
        "type": "bubble",
        "size": "mega",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                        {"type": "text", "text": "aRest", "weight": "bold", "color": "#2563EB", "size": "md"},
                        {"type": "text", "text": "ตรวจรายการตลาดสด (AI Live)", "weight": "bold", "size": "xl", "color": "#0F172A", "flex": 3}
                    ]
                },
                {
                    "type": "text",
                    "text": f"สกัดจากภาพถ่ายจริง | วันที่: 12 ต.ค. 2026",
                    "size": "sm",
                    "color": "#64748B",
                    "margin": "sm"
                },
                {
                    "type": "box",
                    "layout": "vertical",
                    "margin": "lg",
                    "paddingAll": "16px",
                    "backgroundColor": "#F8FAFC",
                    "cornerRadius": "16px",
                    "contents": [
                        {"type": "text", "text": "สรุปข้อมูลที่ AI อ่านได้จากลายมือ", "size": "sm", "color": "#475569"},
                        {
                            "type": "text",
                            "text": hero_text,
                            "size": "xl",
                            "weight": "bold",
                            "color": "#1E3A8A",
                            "margin": "sm"
                        }
                    ]
                },
                {
                    "type": "text",
                    "text": f"รายการวัตถุดิบที่ตรวจพบ (ทั้งหมด {len(items)} รายการ):",
                    "size": "sm",
                    "color": "#64748B",
                    "margin": "lg",
                    "weight": "bold"
                },
                {
                    "type": "box",
                    "layout": "vertical",
                    "contents": item_rows
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "button",
                    "action": {
                        "type": "uri",
                        "label": "🔍 ตรวจทาน / ใส่ราคา",
                        "uri": LIFF_URL
                    },
                    "style": "primary",
                    "color": "#16A34A",
                    "height": "md"
                }
            ]
        }
    }
    return bubble

@app.post("/webhook")
async def line_webhook(request: Request, bg: BackgroundTasks, x_line_signature: str = Header(None)):
    body = (await request.body()).decode("utf-8")
    try:
        payload = json.loads(body)
        for ev in payload.get("events", []):
            reply_token = ev.get("replyToken")
            msg_obj = ev.get("message", {})
            msg_type = msg_obj.get("type")
            msg_id = msg_obj.get("id")
            
            if reply_token:
                if msg_type == "text":
                    bg.add_task(handle_text_reply, reply_token)
                elif msg_type == "image":
                    print(f"[Real Image Event] ID: {msg_id}", flush=True)
                    bg.add_task(handle_slip_image_reply, reply_token, msg_id)
    except Exception as e:
        print(f"[Webhook Error]: {e}", flush=True)

    return JSONResponse({"status": "ok"})

async def handle_text_reply(reply_token: str):
    config = Configuration(access_token=LINE_TOKEN)
    async with AsyncApiClient(config) as api_client:
        msg_api = AsyncMessagingApi(api_client)
        try:
            reply_text = "👋 สวัสดีครับ! aRest AI พร้อมอ่านบิลสดๆ แล้วครับ\n\n📸 ถ่ายรูปบิลหรือลายมือส่งเข้ามาได้เลยครับ ระบบจะอ่านและดึงรายการจริงออกมาให้ทันที!"
            await msg_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=reply_text)]
                )
            )
        except Exception as e:
            print(f"Text reply error: {e}", flush=True)

async def handle_slip_image_reply(reply_token: str, message_id: str):
    config = Configuration(access_token=LINE_TOKEN)
    async with AsyncApiClient(config) as api_client:
        msg_api = AsyncMessagingApi(api_client)
        blob_api = AsyncMessagingApiBlob(api_client)
        
        try:
            print(f"Downloading real image {message_id} from LINE...", flush=True)
            img_bytes = await blob_api.get_message_content(message_id)
            
            # เรียก Gemini 1.5 Flash อ่านภาพสดๆ
            extracted_data = extract_receipt_with_gemini(img_bytes)
            
            if "error" in extracted_data:
                err_msg = extracted_data["error"]
                print(f"[AI Failed]: {err_msg}", flush=True)
                await msg_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=reply_token,
                        messages=[TextMessage(text=f"⚠️ ขออภัยครับ AI ไม่สามารถอ่านภาพได้: {err_msg}")]
                    )
                )
                return
                
            flex_content = build_receipt_flex(extracted_data)
            await msg_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[
                        FlexMessage(alt_text="สรุปรายการตลาดสด aRest", contents=FlexContainer.from_dict(flex_content))
                    ]
                )
            )
            print("Successfully replied with REAL DYNAMIC items!", flush=True)
            
        except Exception as e:
            print(f"Reply error: {e}", flush=True)
            try:
                await msg_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=reply_token,
                        messages=[TextMessage(text=f"⚠️ เกิดข้อผิดพลาดในการประมวลผลภาพ: {str(e)}")]
                    )
                )
            except: pass

@app.get("/")
def home(): return {"status": "aRest Bot Dynamic AI is Live!"}
