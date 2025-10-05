from fastapi import APIRouter, Request, HTTPException
from app.models.user import User
from svix.webhooks import Webhook
import os, json, logging
from datetime import datetime
from app.utils.db import init_db

router = APIRouter()
logger = logging.getLogger("uvicorn.error")


# Ensure MongoDB (Beanie) initialization before using models
async def ensure_db_initialized():
    try:
        await init_db()
        logger.info("✅ Beanie DB initialized successfully.")
    except Exception as e:
        logger.error(f"⚠️ Beanie initialization failed: {repr(e)}")
        raise HTTPException(status_code=500, detail="Database initialization failed")


@router.post("/clerk")
async def handle_clerk_webhook(request: Request):
    webhook_secret = os.getenv("CLERK_WEBHOOK_SECRET")
    if not webhook_secret:
        raise HTTPException(status_code=500, detail="CLERK_WEBHOOK_SECRET not configured")

    body = await request.body()
    payload = body.decode("utf-8")
    headers = dict(request.headers)

    # Check for required Svix headers
    if not all(h in headers for h in ["svix-id", "svix-timestamp", "svix-signature"]):
        logger.error("❌ Missing required Svix headers")
        raise HTTPException(status_code=400, detail="Missing Svix headers")

    try:
        # Ensure database is ready
        await ensure_db_initialized()

        # Verify webhook signature
        wh = Webhook(webhook_secret)
        wh.verify(payload, headers)

        # Parse the webhook event
        event = json.loads(payload)

        if event.get("type") != "user.created":
            logger.info(f"ℹ️ Ignored event type: {event.get('type')}")
            return {"status": "ignored"}

        user_data = event.get("data", {})

        # Safe extraction of nested arrays
        email_addresses = user_data.get("email_addresses") or []
        phone_numbers = user_data.get("phone_numbers") or []

        email = email_addresses[0].get("email_address") if email_addresses else None
        phone = phone_numbers[0].get("phone_number") if phone_numbers else None
        first_name = user_data.get("first_name")
        last_name = user_data.get("last_name")
        profile_img = user_data.get("profile_image_url")
        clerk_id = user_data.get("id")

        # Skip if essential data missing
        if not clerk_id or not email:
            logger.warning(f"⚠️ Skipping user with missing essential data: {clerk_id}")
            return {"status": "ignored"}

        # Check if user exists
        user = await User.find_one(User.clerk_id == clerk_id)
        now = datetime.utcnow()

        if user:
            # Update existing user safely
            user.email = email
            user.first_name = first_name or user.first_name
            user.last_name = last_name or user.last_name
            user.phone_number = phone or user.phone_number
            user.profile_image_url = profile_img or user.profile_image_url
            user.updated_at = now
            await user.save()
            logger.info(f"🔁 Updated existing user: {clerk_id}")
        else:
            # Create new user
            new_user = User(
                clerk_id=clerk_id,
                email=email,
                first_name=first_name,
                last_name=last_name,
                phone_number=phone,
                profile_image_url=profile_img,
                created_at=now,
                updated_at=now,
            )
            await new_user.insert()
            logger.info(f"🆕 Created new user: {clerk_id}")

        return {"status": "success"}

    except Exception as e:
        logger.error(f"❌ Webhook processing failed: {repr(e)}")
        raise HTTPException(status_code=401, detail=f"Webhook processing failed: {str(e)}")