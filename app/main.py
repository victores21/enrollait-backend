from fastapi import FastAPI, Depends
from app.api.routes import health
from app.api.routes import integrations

app = FastAPI(title="Enrollait API")
app.include_router(health.router, tags=["Health"])
app.include_router(integrations.router, tags=["Integrations"])
