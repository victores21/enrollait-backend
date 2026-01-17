from fastapi import FastAPI, Depends
import os
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health
from app.api.routes import integrations
from app.api.routes import moodle_users
from app.api.routes import enrollments
from app.api.routes import products
from app.api.routes import product_courses
from app.api.routes import stripe_config
from app.api.routes import stripe_webhooks
from app.api.routes import tenant
from app.api.routes import stripe_checkout


app = FastAPI(title="Enrollait API")

origins_env = os.getenv("CORS_ORIGINS", "http://localhost:3000")
origins = [o.strip() for o in origins_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,          # or ["*"] for quick dev only
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(health.router, tags=["Health"])
app.include_router(integrations.router, tags=["Integrations"])
app.include_router(moodle_users.router, tags=["Moodle Users", 'Moodle'])
app.include_router(enrollments.router, tags=["Enrollments", 'Moodle'])
app.include_router(products.router, tags=["Products"])
app.include_router(product_courses.router, tags=["Product Courses"])
app.include_router(stripe_config.router, tags=["Stripe Config"])
app.include_router(stripe_webhooks.router, tags=["Stripe Webhooks"])
app.include_router(tenant.router, tags=["Tenant"])
app.include_router(stripe_checkout.router, tags=["Stripe Checkout"])


