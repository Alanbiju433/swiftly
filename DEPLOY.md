# Swiftly — GitHub + Render Deployment Guide

Follow these steps to get Swiftly live on the internet for free.

---

## Step 1 — Install Git (if you haven't)

Download from: https://git-scm.com/download/win  
Install with default settings. Open a new Command Prompt after.

---

## Step 2 — Push your code to GitHub

### 2a. Create a GitHub account
Go to https://github.com and sign up (free).

### 2b. Create a new repository
1. Click the **+** icon → **New repository**
2. Name it: `swiftly`
3. Set to **Public** (required for free Render)
4. Click **Create repository**

### 2c. Push your code

Open Command Prompt, navigate to your Swiftly folder, and run these commands one by one:

```bash
cd path\to\your\swiftly
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/swiftly.git
git push -u origin main
```

Replace `YOUR_USERNAME` with your GitHub username.

---

## Step 3 — Deploy on Render

### 3a. Create a Render account
Go to https://render.com and sign up with your GitHub account (free).

### 3b. Create a new Web Service
1. From your Render dashboard, click **New** → **Web Service**
2. Click **Connect a repository** and select your `swiftly` repo
3. Fill in the settings:

| Setting | Value |
|---|---|
| **Name** | swiftly (or anything you like) |
| **Region** | Choose closest to you |
| **Branch** | main |
| **Runtime** | Python 3 |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `gunicorn app:app` |
| **Instance Type** | **Free** |

4. Click **Create Web Service**

### 3c. Add environment variable
In your Render service settings, go to **Environment** → **Add Environment Variable**:

| Key | Value |
|---|---|
| `SECRET_KEY` | Any long random string, e.g. `swiftly-super-secret-2025-xyz123` |

### 3d. Wait for deployment
Render will build and deploy automatically. It takes 2–5 minutes.  
Your live URL will appear at the top: `https://swiftly.onrender.com`

---

## Step 4 — Access your live app

| Role | URL |
|---|---|
| Customer | `https://your-app.onrender.com/` |
| Manager | `https://your-app.onrender.com/manager` |
| Driver | `https://your-app.onrender.com/driver` |

Demo login: any of the seed accounts, password: **demo123**

---

## Important notes

### Free tier limitations
- **Render free tier spins down after 15 minutes of inactivity** — the first request after sleep takes ~30 seconds to wake up. This is normal.
- **SQLite data resets on redeploy** — the free tier has no persistent disk. Every time you redeploy, the database resets to demo data. To avoid this, either:
  - Upgrade to Render's paid plan and add a persistent disk, OR
  - Migrate to [Supabase](https://supabase.com) (free PostgreSQL) and swap `sqlite3` for `psycopg2`

### Making updates
After changing code, push again:
```bash
git add .
git commit -m "Update: describe your change"
git push
```
Render auto-deploys on every push.

### Custom domain
In Render → **Settings** → **Custom Domains**, you can add your own domain (e.g. `getswiftly.com`).

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Build fails | Check `requirements.txt` has all packages |
| App crashes on start | Check logs in Render → **Logs** tab |
| "Internal Server Error" | Usually a missing template file — check all 5 HTML files are committed |
| Database errors | Delete `swiftly.db` from git (`git rm --cached swiftly.db`), add `swiftly.db` to `.gitignore` |

### Add swiftly.db to .gitignore
Create a file called `.gitignore` in your swiftly folder with:
```
swiftly.db
__pycache__/
*.pyc
.env
```
