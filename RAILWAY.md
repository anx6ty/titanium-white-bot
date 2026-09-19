# Railway deployment

This repository is configured to deploy the dashboard on [Railway](https://railway.app/) using the root `Dockerfile`.

## Deploy

1. Create a new Railway project.
2. Choose **Deploy from GitHub repo** and select `anx6ty/titanium-white-bot`.
3. Railway will detect `Dockerfile` automatically.
4. Deploy the service. Railway provides the `PORT` variable automatically.

The Docker image builds the Next.js app from `dashboard/` and starts it with the Railway-provided port.

## Optional variables

No environment variables are required for the Open-Meteo weather dashboard. It uses the public Open-Meteo geocoding and forecast APIs from the browser.

For a custom domain, open the service in Railway and use **Settings → Networking → Generate Domain**.

## Local Docker test

```bash
docker build -t titanium-white-dashboard .
docker run --rm -p 3000:3000 -e PORT=3000 titanium-white-dashboard
```
