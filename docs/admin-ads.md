# Campus flyer ads

Flyers are managed from the signed-in admin account at `/admin/ads`. Only an account with `is_admin = true` can upload or delete a flyer. Uploaded images use the configured R2 bucket and the public carousel only returns active records.

## Create or promote the administrator account

From `backend/backend`, set these environment variables to values you choose. Do not commit the password or put it in a shared `.env` file:

```powershell
$env:ADMIN_EMAIL = "your-admin-email@example.com"
$env:ADMIN_PASSWORD = "use-a-unique-password-at-least-12-characters"
$env:ADMIN_FULL_NAME = "Admin name"
.\venv\Scripts\python.exe -m scripts.create_admin
```

The script creates a verified external account if the email is new. If that email already belongs to an account, it promotes it only when the supplied password matches the existing account.

## Configure and run

The backend needs its normal database and R2 configuration (`DATABASE_URL`, `JWT_SECRET`, `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, and `R2_PUBLIC_URL`).

Apply the schema migration and start the backend:

```powershell
.\venv\Scripts\python.exe -m alembic upgrade head
.\venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Sign in to the website with the admin account, then open `/admin/ads`. Choose a JPG, PNG, or WebP flyer (maximum 3 MB), provide image text for accessibility, and optionally add an `http://` or `https://` destination link. Delete a flyer from the same page to remove it from the public carousel.
