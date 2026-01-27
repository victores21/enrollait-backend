from fastapi import FastAPI, Depends
import os
from fastapi.middleware.cors import CORSMiddleware


from app.api.routes import health
from app.api.routes import integrations
from app.api.routes import products
from app.api.routes import stripe_config
from app.api.routes import stripe_webhooks
from app.api.routes import stripe_checkout
from app.api.routes import categories
from app.api.routes import courses
from app.api.routes import orders
from app.api.routes import admin_auth
from app.api.routes import admin_users


app = FastAPI(title="Enrollait API")

origins_env = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
origins = [o.strip().rstrip("/") for o in origins_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # exact matches
    allow_origin_regex=r"^https?://([a-z0-9-]+\.)*localhost(:\d+)?$|^https?://127\.0\.0\.1(:\d+)?$|^https?://([a-z0-9-]+\.)*enrollait\.com(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(health.router, tags=["Health"])
app.include_router(integrations.router, tags=["Integrations"])
app.include_router(products.router, tags=["Products"])
app.include_router(stripe_config.router, tags=["Stripe Config"])
app.include_router(stripe_webhooks.router, tags=["Stripe Webhooks"])
app.include_router(stripe_checkout.router, tags=["Stripe Checkout"])
app.include_router(categories.router, tags=["Categories"])
app.include_router(courses.router, tags=["Courses"])
app.include_router(orders.router, tags=["Orders"])
app.include_router(admin_auth.router, tags=["Admin Auth"])
app.include_router(admin_users.router, tags=["Admin Users"])

