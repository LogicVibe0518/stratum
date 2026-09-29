# Deploying Stratum on Railway

One image runs both services. Railway gives each service its own start command and its own variables. The API and the worker stay separate so the API's environment never contains the service-role key or the JWT secret. The worker's environment does.

## 1. Create the project

In the Railway dashboard, create a new project and choose **Deploy from GitHub repo**. Pick `LogicVibe0518/stratum`. Set the region to **EU West**, the closest region to the Supabase project in London.

## 2. Two services from the same repo

Add two services from that repo. In each service's Settings, set **Config file path**:

- API service: `infra/railway/api.json`
- Worker service: `infra/railway/worker.json`

Both services build `Dockerfile` at the repo root. The API starts with the image's default command. The worker config overrides that command with `stratum-queue --factory stratum_worker.queue.identity:from_env --concurrency 4`.

## 3. Variables

On the **API** service, set:

- `SUPABASE_URL`
- `SUPABASE_ANON_KEY`
- `OPENROUTER_API_KEY`
- `OPENAI_API_KEY`
- `FAL_KEY`
- the `LANGFUSE_*` keys (`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`) if Langfuse is used

On the **worker** service, set the same variables, plus `SUPABASE_SERVICE_ROLE_KEY` and `SUPABASE_JWT_SECRET`.

Never give the API `SUPABASE_SERVICE_ROLE_KEY` or `SUPABASE_JWT_SECRET`. Those two belong only to the worker.

## 4. The link you open

On the API service, open Networking and choose **Generate domain**. That URL is what you open.

## 5. Check

Open `https://<domain>/healthz`. It should show `"status": "ok"`, and every task should have a provider. The worker's logs should show it polling, with no errors.

## 6. Supabase

Move the Supabase project to Pro before you log in, so it never pauses.
