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

    # Read raw body bytes
    body = await request.body()

    # FastAPI headers are case-insensitive; map signature correctly
    signature_header = request.headers.get("clerk-signature")
    if not signature_header:
        logger.error("Missing Clerk-Signature header")
        raise HTTPException(status_code=400, detail="Missing Clerk-Signature header")

    logger.info(f"Webhook received: {len(body)} bytes")

    try:
        # Ensure DB is initialized before processing
        await ensure_db_initialized()

        # Verify webhook signature
        wh = Webhook(webhook_secret)
        wh.verify(body, {"Clerk-Signature": signature_header})

        # Decode payload only after verification
        data = json.loads(body)

        # Only process 'user.created' events
        if data.get("type") != "user.created":
            logger.info("Webhook ignored: not a user.created event")
            return {"status": "ignored"}

        user_data = data.get("data", {})
        clerk_id = user_data.get("id")
        email = user_data.get("email_addresses", [{}])[0].get("email_address")
        first_name = user_data.get("first_name")
        last_name = user_data.get("last_name")
        phone = user_data.get("phone_numbers", [{}])[0].get("phone_number")
        profile_img = user_data.get("profile_image_url")

        # Upsert: update existing or insert new user
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
