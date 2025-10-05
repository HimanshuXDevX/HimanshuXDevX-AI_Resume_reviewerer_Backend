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

    # Get raw body and signature header
    body = await request.body()
    signature_header = request.headers.get("clerk-signature")

    if not signature_header:
        logger.error("❌ Missing Clerk-Signature header")
        raise HTTPException(status_code=400, detail="Missing Clerk-Signature header")

    try:
        # Ensure DB is ready
        await ensure_db_initialized()

        # Verify Clerk webhook
        wh = Webhook(webhook_secret)
        wh.verify(body, {"Clerk-Signature": signature_header})

        # Decode verified payload
        event = json.loads(body)
        event_type = event.get("type")
        data = event.get("data", {})

        # Only process user.created event
        if event_type != "user.created":
            logger.info(f"ℹ️ Ignored event type: {event_type}")
            return {"status": "ignored"}

        # Extract user details
        clerk_id = data.get("id")
        email = data.get("email_addresses", [{}])[0].get("email_address")
        first_name = data.get("first_name")
        last_name = data.get("last_name")
        phone = data.get("phone_numbers", [{}])[0].get("phone_number")
        profile_img = data.get("profile_image_url")

        # Check if user already exists
        user = await User.find_one(User.clerk_id == clerk_id)

        if user:
            # Update user
            user.email = email
            user.first_name = first_name
            user.last_name = last_name
            user.phone_number = phone
            user.profile_image_url = profile_img
            user.updated_at = datetime.utcnow()
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
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            await new_user.insert()
            logger.info(f"🆕 Created new user: {clerk_id}")

        return {"status": "success"}

    except Exception as e:
        logger.error(f"❌ Webhook processing failed: {repr(e)}")
        raise HTTPException(status_code=401, detail=f"Webhook processing failed: {str(e)}")
