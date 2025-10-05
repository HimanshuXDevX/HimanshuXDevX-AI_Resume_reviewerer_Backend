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

    # Read the raw body (must remain unmodified for Svix verification)
    body = await request.body()
    payload = body.decode("utf-8")
    headers = dict(request.headers)

    # Check for presence of Svix headers
    if not all(h in headers for h in ["svix-id", "svix-timestamp", "svix-signature"]):
        logger.error("❌ Missing required Svix headers")
        raise HTTPException(status_code=400, detail="Missing Svix headers")

    try:
        # Ensure database is ready
        await ensure_db_initialized()

        # Verify the webhook signature
        wh = Webhook(webhook_secret)
        wh.verify(payload, headers)

        # Parse and process event
        event = json.loads(payload)

        if event.get("type") != "user.created":
            logger.info(f"ℹ️ Ignored event type: {event.get('type')}")
            return {"status": "ignored"}

        user_data = event.get("data", {})

        # Extract user details from the nested `data` field
        clerk_id = user_data.get("id")
        email = user_data.get("email_addresses", [{}])[0].get("email_address")
        first_name = user_data.get("first_name")
        last_name = user_data.get("last_name")
        phone = user_data.get("phone_numbers", [{}])[0].get("phone_number")
        profile_img = user_data.get("profile_image_url")

        # Ensure essential fields exist
        if not clerk_id or not email:
            logger.error("⚠️ Invalid user data received from Clerk webhook")
            raise HTTPException(status_code=400, detail="Invalid user data")

        # Check if user already exists
        user = await User.find_one(User.clerk_id == clerk_id)
        now = datetime.utcnow()

        if user:
            # Update existing user
            user.email = email
            user.first_name = first_name
            user.last_name = last_name
            user.phone_number = phone
            user.profile_image_url = profile_img
            user.updated_at = now
            await user.save()
            logger.info(f"🔁 Updated existing user: {clerk_id}")
        else:
            # Create a new user document
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