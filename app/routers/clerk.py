from fastapi import APIRouter, Request, HTTPException
from app.models.user import User
from svix.webhooks import Webhook
import os, json
from datetime import datetime
from app.utils.db import init_db
import logging

router = APIRouter()
logger = logging.getLogger("uvicorn.error")

async def ensure_db_initialized():
    try:
        await init_db()
        logger.info("✅ Beanie DB initialized.")
    except Exception as e:
        logger.warning(f"⚠️ DB initialization failed: {repr(e)}")

@router.post("/clerk")
async def handle_clerk_webhook(request: Request):
    webhook_secret = os.getenv("CLERK_WEBHOOK_SECRET")
    if not webhook_secret:
        raise HTTPException(status_code=500, detail="Webhook secret not configured")

    # Read raw bytes
    body = await request.body()
    headers = dict(request.headers)
    signature_header = headers.get("Clerk-Signature") or headers.get("clerk-signature")

    logger.info(f"Webhook headers: {headers}")
    logger.info(f"Payload length: {len(body)} bytes")

    try:
        await ensure_db_initialized()

        # Verify webhook using raw bytes and normalized header
        wh = Webhook(webhook_secret)
        wh.verify(body, {"Clerk-Signature": signature_header})

        # Decode after verification
        data = json.loads(body)

        if data.get("type") != "user.created":
            logger.info("Webhook event ignored: not user.created")
            return {"status": "ignored"}

        # Extract fields from Clerk payload
        user_data = data.get("data", {})
        clerk_id = user_data.get("id")
        email = user_data.get("email_addresses", [{}])[0].get("email_address")
        first_name = user_data.get("first_name")
        last_name = user_data.get("last_name")
        phone = user_data.get("phone_numbers", [{}])[0].get("phone_number")
        profile_img = user_data.get("profile_image_url")

        # Upsert to avoid duplicates if webhook is re-sent
        existing = await User.find_one(User.clerk_id == clerk_id)
        if existing:
            existing.email = email
            existing.first_name = first_name
            existing.last_name = last_name
            existing.phone_number = phone
            existing.profile_image_url = profile_img
            existing.updated_at = datetime.utcnow()
            await existing.save()
            logger.info(f"Updated existing user {clerk_id}")
        else:
            new_user = User(
                clerk_id=clerk_id,
                email=email,
                first_name=first_name,
                last_name=last_name,
                phone_number=phone,
                profile_image_url=profile_img,
            )
            await new_user.insert()
            logger.info(f"Inserted new user {clerk_id}")

        return {"status": "success"}

    except Exception as e:
        logger.error(f"Webhook verification or processing failed: {repr(e)}")
        raise HTTPException(status_code=401, detail=str(e))
