# Railway deployment

The root Dockerfile is configured for Railway. Connect this GitHub repository in Railway and deploy the service; Railway supplies `PORT` automatically.

The dashboard is a live weather app using Open-Meteo's public geocoding and forecast APIs. No API key or environment variables are required.

## Local

```bash
cd dashboard
npm install
npm run dev
```
