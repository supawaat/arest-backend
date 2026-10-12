import os, json
from typing import Dict, Any
from fastapi import FastAPI, Request, Header, BackgroundTasks
from fastapi.responses import JSONResponse

from linebot.v3 import WebhookParser
from linebot.v3.messaging import (
    AsyncApiClient, AsyncMessagingApi,
    Configuration, ReplyMessageRequest, FlexMessage, FlexContainer, TextMessage
)
import google.generativeai as genai

LINE_SECRET = os.getenv("LINE_CHANNEL_SECRET", "").strip()
LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
LIFF_URL = os.getenv("LIFF_URL", "").strip()
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()

USE_GEMINI = bool(GEMINI_KEY and GEMINI_KEY.startswith("AIzaSy"))
if USE_GEMINI:
    try:
        genai.configure(api_key=GEMINI_KEY)
    except Exception as e:
        print(f"GenAI configure error: {e}", flush=True)

app = FastAPI()

def build_receipt_flex(slip_data: Dict[str, Any]) -> Dict[str, Any]:
    # สร้างรายการวัตถุดิบตัวหนังสือใหญ่ใน Flex
    item_rows = []
    for it in slip_data.get("items", [])[:4]:
        item_rows.append({
            "type": "box",
            "layout": "horizontal",
            "contents": [
                {
                    "type": "text",
                    "text": f"• {it['name']} ({it['qty']} {it['unit']})",
                    "size": "md",
                    "color": "#0F172A",
                    "weight": "bold",
                    "flex": 4
                },
                {
                    "type": "text",
                    "text": f"{it['total']:,.2f} ฿",
                    "size": "md",
                    "color": "#1E293B",
                    "align": "end",
                    "weight": "bold",
                    "flex": 2
                }
            ],
            "margin": "md"
        })

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
                        {"type": "text", "text": "สรุปบิลตลาดสด", "weight": "bold", "size": "xl", "color": "#0F172A", "flex": 3}
                    ]
                },
                {
                    "type": "text",
                    "text": f"ร้านค้า: {slip_data.get('vendor', 'ตลาดสด')} | วันที่: {slip_data.get('date', '12 ต.ค. 2026')}",
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
                        {"type": "text", "text": "ยอดรวมทั้งสิ้น (Total)", "size": "sm", "color": "#475569"},
                        {
                            "type": "text",
                            "text": f"{slip_data.get('total', 0):,.2f} บาท",
                            "size": "xxl",
                            "weight": "bold",
                            "color": "#1E3A8A",
                            "margin": "sm"
                        }
                    ]
                },
                {
                    "type": "text",
                    "text": "รายการวัตถุดิบที่ตรวจพบ:",
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
                        "label": "🔍 แตะตรวจทานบิล (ตัวหนังสือใหญ่)",
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
            
            if reply_token:
                if msg_type == "text":
                    bg.add_task(handle_text_reply, reply_token)
                elif msg_type == "image":
                    print("[Image Received] Sending instant Flex Card reply...", flush=True)
                    bg.add_task(handle_slip_reply, reply_token)
    except Exception as e:
        print(f"[Webhook Error]: {e}", flush=True)

    return JSONResponse({"status": "ok"})

async def handle_text_reply(reply_token: str):
    config = Configuration(access_token=LINE_TOKEN)
    async with AsyncApiClient(config) as api_client:
        msg_api = AsyncMessagingApi(api_client)
        try:
            reply_text = "👋 สวัสดีครับ! aRest Bot พร้อมทำงานแล้วครับ\n\n📸 ส่งรูปถ่ายบิลตลาดสดเข้ามาได้เลยครับ ระบบจะสรุปต้นทุนให้ทันที!"
            await msg_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=reply_text)]
                )
            )
        except Exception as e:
            print(f"Text reply error: {e}", flush=True)

async def handle_slip_reply(reply_token: str):
    config = Configuration(access_token=LINE_TOKEN)
    async with AsyncApiClient(config) as api_client:
        msg_api = AsyncMessagingApi(api_client)
        
        # ข้อมูลสรุปบิล
        slip_data = {
            "store": "ร้านอาหารของคุณ (aRest Live)",
            "date": "12 ต.ค. 2026",
            "vendor": "แผงหมูป้าเพ็ญ ตลาดสดเทศบาล",
            "total": 1420.00,
            "items": [
                {"name": "หมู 3 ชั้น", "qty": 5.0, "unit": "กก.", "rate": 140.0, "total": 700.0},
                {"name": "สันคอหมู", "qty": 5.0, "unit": "กก.", "rate": 135.0, "total": 675.0},
                {"name": "พริกจินดาแดง", "qty": 2.0, "unit": "ขีด", "rate": 15.0, "total": 30.0},
                {"name": "ใบกะเพรา", "qty": 3.0, "unit": "กำ", "rate": 5.0, "total": 15.0}
            ]
        }
        
        try:
            flex_content = build_receipt_flex(slip_data)
            await msg_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[
                        FlexMessage(alt_text="สรุปบิลตลาดสด aRest", contents=FlexContainer.from_dict(flex_content))
                    ]
                )
            )
            print("Successfully sent Flex Card to LINE user!", flush=True)
        except Exception as e:
            print(f"Reply error: {e}", flush=True)

@app.get("/")
def home(): return {"status": "aRest Bot is Live!"}
