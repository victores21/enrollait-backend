# Enrollait / Instalait Backend (FastAPI)

This is the backend API for the MVP:

- Sync courses from Moodle
- Create Stripe Checkout sessions
- Stripe webhook → create/find Moodle user → enroll → send emails

---

pip freeze > requirements.txt

## 1) Requirements

- Python 3.11+
- (Recommended) virtualenv
- Supabase project (Postgres) + `DATABASE_URL`
- Stripe account (test keys for dev)
- Moodle instance with Web Services enabled + token
- Install requirements pip install -r requirements.txt

---

## 2) First-time setup (recommended)

### Create and activate a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
{"email":"admin@uam.com","password":"MyStrongPass123"}
```
