from fastapi import FastAPI, Depends
from app.api.routes import health
from app.api.routes import integrations
from app.api.routes import moodle_users
from app.api.routes import enrollments
from app.api.routes import products
from app.api.routes import product_courses

app = FastAPI(title="Enrollait API")
app.include_router(health.router, tags=["Health"])
app.include_router(integrations.router, tags=["Integrations"])
app.include_router(moodle_users.router, tags=["Moodle Users", 'Moodle'])
app.include_router(enrollments.router, tags=["Enrollments", 'Moodle'])
app.include_router(products.router, tags=["Products", 'Moodle'])
app.include_router(product_courses.router, tags=["Product Courses", 'Moodle'])


