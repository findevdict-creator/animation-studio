# Running the studio on your own machine

Everything runs on one computer. Nothing is hosted anywhere. "The web app" below is the
studio's screen, served from your machine and opened in your browser at `localhost`.

The whole studio runs in Docker: the database, Redis, object storage (SeaweedFS), the login
server (Keycloak), the API and the Blender, render, publisher, media and AI workers. The web
app runs from source with Node. You do not need to install Blender, FFmpeg or Rhubarb
yourself, because they are inside the worker images.

## 1. What you need

| | |
| --- | --- |
| **Docker** | Docker Desktop (macOS, Windows) or Docker Engine with the Compose v2 plugin (Linux). In Docker Desktop, give it **at least 8 GB of memory, 12–16 GB is better** (Settings → Resources), and about 10 GB of free disk for the images. |
| **Git** | To clone the repository. |
| **Node.js 22** | With npm, for the web app. |
| **Python 3.10+** | Only for the demo scripts in `scripts/`, which use the standard library alone. |
| **FFmpeg** | Only for `scripts/phase8_acceptance.py`, which makes a music track and checks the MP4 with `ffmpeg` and `ffprobe`. The studio itself does not need it on your machine. |
| **An AI key (optional)** | Only for the "Ask the AI" buttons (character, outfit, script and storyboard suggestions). Everything else works without one. |

Ports 5173 (web), 8000 (API), 8080 (Keycloak) and 8333 (storage) must be free.

**Apple Silicon Macs (M1–M4):** Blender's Python module has no Linux ARM build, so the
worker images must be built for Intel and run under emulation. Before the build in step 3, run
`export DOCKER_DEFAULT_PLATFORM=linux/amd64` in the same terminal. In Docker Desktop, also turn
on Settings → General → "Use Rosetta for x86_64/amd64 emulation". It works, but Blender jobs
are noticeably slower than on an Intel or AMD machine.

## 2. Get the code

The newest work is on the last branch of the stack, `v2-42-phase8-acceptance`. It contains
every earlier branch.

```
git clone https://github.com/findevdict-creator/animation-studio.git
cd animation-studio
git checkout v2-42-phase8-acceptance
```

## 3. Start the back end

Optional: to use an AI provider, copy the settings file and put your key in it.

```
cp infra/env/.env.example infra/.env
# then edit infra/.env:  STUDIO_AI_PROVIDER=anthropic  and  ANTHROPIC_API_KEY=sk-ant-...
#   (openai, gemini, deepseek and openai_compatible work too, with their own key variable)
```

Build and start everything:

```
docker compose -f infra/compose.yaml up -d --build
```

The first build downloads Python, Blender (as the `bpy` module, about 400 MB), FFmpeg,
espeak-ng and Rhubarb Lip Sync, and takes 5–15 minutes. Later starts take under a minute.
`storage-init` and `migrate` run once and exit. Everything else stays up. Check with:

```
docker compose -f infra/compose.yaml ps
```

`api`, `keycloak`, `postgres`, `redis`, `seaweedfs`, `worker-blender`, `worker-render`,
`worker-publisher`, `worker-media`, `worker-ai` and `beat` should be "running" (or "healthy").
The API docs are at http://localhost:8000/api/docs.

## 4. Start the web app

In a second terminal:

```
cd web
npm ci
npm run dev
```

Open **http://localhost:5173**. You are sent to the Keycloak login page. The dev users are:

| User | Password | Can |
| --- | --- | --- |
| `creator` | `dev-creator` | make characters, outfits, accessories, props, environments, animations and audio, and submit them for review |
| `reviewer` | `dev-reviewer` | approve or reject submitted versions |
| `director` | `dev-director` | review and publish versions; make scenes, series and episodes, and render them |
| `admin` | `dev-admin` | everything |

The Keycloak admin console is http://localhost:8080 (`admin` / `admin`).

## 5. Put something in the library

A new install has an empty library. You can build assets by hand in the web app (Characters →
New, then Outfits, Environments …), or let the demo scripts make a ready-made set. From the
repository root:

```
python3 scripts/phase7_acceptance.py --out phase7-output
```

In about 10–15 minutes this publishes two characters (Ali and Maya), an outfit for each,
four props and a living room, and assembles a scene with them. Note the codes it prints:
`CHAR-…`, `OUTFIT-…` and `ENV-…`.

Then you can make an episode in the web app (Series → New series, cast Ali and Maya, add the
living room, then New episode → Script → Storyboard → Episode versions → Render). Or run the
Phase 8 script, which does all of that and renders the episode twice. At 640×360 it took
25 minutes on a 4-core test machine:

```
python3 scripts/phase8_acceptance.py --out phase8-output --width 640 --height 360 \
  --ali CHAR-001 --maya CHAR-002 --ali-outfit OUTFIT-001 --maya-outfit OUTFIT-002 --env ENV-001
```

Use the codes the Phase 7 script printed. On an empty library they are the ones above. The finished MP4 is in
`phase8-output/render-1/episode.mp4`, and in the web app under Series → the episode → Render.

## Rendering speed and your GPU

Episode renders use Cycles **on the CPU**. These are measured times on a 4-core machine (a faster CPU will be quicker):

| Size | Samples | Seconds per frame | 7.5 s episode (181 frames) |
| --- | --- | --- | --- |
| 640×360 | 4 | about 3.3 | about 11 minutes |
| 1920×1080 | 4 | about 20 | about an hour |

For quick tries, set the series' render size to 640×360.

**An NVIDIA GPU is not used yet.** The workers set Cycles to the CPU, and the worker image
has no GPU libraries. Eevee is accepted as a render setting, but it needs a GPU or EGL context
inside the worker, which the default image does not have. Using the GPU needs:

* the NVIDIA Container Toolkit on Linux, or Docker Desktop with WSL 2 on Windows;
* a GPU reservation on `worker-render` in compose;
* Cycles set to CUDA or OptiX;
* the EGL libraries in the image, for Eevee.

None of this has been built or tested, because the build machine has no GPU. Until it is
done, the studio runs fine on a GPU machine, but it renders on the CPU.

## On Windows

Use Docker Desktop with the WSL 2 backend. The commands above work in PowerShell, with two
changes: write `python` instead of `python3`, and put the Phase 8 command on one line (drop
the `\`). Or run them inside a WSL Ubuntu terminal unchanged.

## Stopping, restarting and starting over

```
docker compose -f infra/compose.yaml stop              # stop, keep everything
docker compose -f infra/compose.yaml up -d             # start again
docker compose -f infra/compose.yaml down -v           # delete everything, including the library
```

After you pull new code, rebuild with `docker compose -f infra/compose.yaml up -d --build`.

## When something goes wrong

* **A job stays queued.** The worker for its queue is not running. Look at it with
  `docker compose -f infra/compose.yaml logs worker-blender` (or `worker-render`,
  `worker-media`, `worker-publisher`, `worker-ai`).
* **The login page loops or says "invalid redirect".** Open the app at exactly
  http://localhost:5173. The Keycloak client only accepts that address.
* **Images or videos don't load in the browser.** The browser fetches files straight from
  storage at http://localhost:8333, so port 8333 must be reachable.
* **"Set ANTHROPIC_API_KEY".** An AI button was used without a key. Add one to `infra/.env`
  (step 3), then run `docker compose -f infra/compose.yaml up -d worker-ai` again.
* **A worker is killed while rendering (exit code 137).** Docker ran out of memory. Give
  Docker Desktop more memory.

## Developing without Docker

To run the API and workers straight from source (for tests and debugging), see "Development"
in the [README](../README.md#development).
