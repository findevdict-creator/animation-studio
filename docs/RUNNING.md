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

Without a GPU, episode renders use Cycles **on the CPU**. These are measured times on a
4-core machine (a faster CPU will be quicker):

| Size | Samples | Seconds per frame | 7.5 s episode (181 frames) |
| --- | --- | --- | --- |
| 640×360 | 4 | about 3.3 | about 11 minutes |
| 1920×1080 | 4 | about 20 | about an hour |

For quick tries, set the series' render size to 640×360.

### Rendering on an NVIDIA GPU

The workers use an NVIDIA GPU when Docker gives them one. Start the stack with the GPU file
added (`infra/compose.gpu.yaml`); without it, everything runs on the CPU as before.

**Windows (Docker Desktop + WSL 2).**

1. Install the current NVIDIA Game Ready or Studio driver for Windows. Do **not** install a
   Linux driver inside WSL; the Windows driver serves WSL too.
2. In PowerShell: `wsl --update`, then restart Docker Desktop.
3. Docker Desktop → Settings → General: "Use the WSL 2 based engine" is on.
4. Check that Docker sees the GPU (prints your card in a table):
   ```
   docker run --rm --gpus all ubuntu nvidia-smi
   ```

**Linux.** Install the NVIDIA driver and the
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html), then:

```
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
docker run --rm --gpus all ubuntu nvidia-smi
```

**Start the studio on the GPU** (rebuild once, because the worker image gained the EGL
libraries for Eevee):

```
docker compose -f infra/compose.yaml -f infra/compose.gpu.yaml up -d --build
```

Use the same two `-f` files for every later `up`, `logs` or `exec` command; a plain
`docker compose -f infra/compose.yaml up -d` puts the workers back on the CPU.

**Check the GPU inside the worker:**

```
docker compose -f infra/compose.yaml -f infra/compose.gpu.yaml exec worker-render nvidia-smi
docker compose -f infra/compose.yaml -f infra/compose.gpu.yaml exec worker-render python -m cartoon_engine.gpu_check --require-gpu
```

`gpu_check` prints the Cycles device the studio will use (`OPTIX (NVIDIA GeForce …)`, or
`CUDA` on cards without RT cores), renders a test scene on the GPU and on the CPU with both
times, and renders it with Eevee, naming the renderer (`NVIDIA …` is the GPU; `llvmpipe` means
software). The very first GPU render compiles kernels and can take a minute or two; they are
cached on the work volume after that.

**What changes on a GPU.**

* Cycles picks OptiX, then CUDA, then falls back to the CPU, from what Blender sees. Each job
  log has a `[studio] cycles device: …` line, the render progress says "cycles on OPTIX (…)",
  and `render_report.json` records `device` and the samples used under `in_force`.
* Final episode frames get at least 64 samples (instead of the series' 4–12), denoised on the
  GPU, with the scene kept loaded between frames. Series asking for more keep their number.
  Previews (characters, outfits, sets, scene previews) keep their low samples, just faster.
* Eevee works. Choose it as the series' render engine for fast drafts. On the CPU stack it
  also works now, but in software and slowly.

Settings, in `infra/.env` or the shell before `up`:

| Variable | Default | |
| --- | --- | --- |
| `STUDIO_CYCLES_DEVICE` | `auto` | `optix`, `cuda` or `cpu` to force one (a missing GPU still falls back to the CPU) |
| `STUDIO_GPU_MIN_SAMPLES` | `64` | samples a final GPU frame gets at least; `0` keeps the series' number |
| `NVIDIA_VISIBLE_DEVICES` | `all` | e.g. `0` to use only the first GPU |

**Compare with your CPU baseline.** The Phase 8 script prints "seconds per frame" for each
shot (also in `<out>/render-1/render_reports.json`). Run it once on the GPU stack and compare
with your CPU run. To keep the samples equal for a fair comparison, set
`STUDIO_GPU_MIN_SAMPLES=0` for that run:

```
# GPU, same 4 samples as the CPU run
STUDIO_GPU_MIN_SAMPLES=0 docker compose -f infra/compose.yaml -f infra/compose.gpu.yaml up -d
python3 scripts/phase8_acceptance.py --out phase8-gpu --width 640 --height 360 \
  --ali CHAR-001 --maya CHAR-002 --ali-outfit OUTFIT-001 --maya-outfit OUTFIT-002 --env ENV-001
```

In PowerShell set the variable first with `$env:STUDIO_GPU_MIN_SAMPLES="0"`, then run
`docker compose …` on its own line, and remove it again with
`Remove-Item Env:STUDIO_GPU_MIN_SAMPLES` before going back to the GPU defaults.

**If the GPU is not found.** `gpu_check` prints what it tried for OptiX and CUDA.

* `nvidia-smi` fails inside the container: Docker is not passing the GPU. Recheck the
  `docker run --rm --gpus all ubuntu nvidia-smi` step above, and that you started with both
  `-f` files.
* `nvidia-smi` works but Cycles says "no device": update the NVIDIA driver (Blender 5.2 needs
  a recent one), then `docker compose … up -d --force-recreate worker-render worker-blender`.
* OptiX fails but CUDA works: set `STUDIO_CYCLES_DEVICE=cuda`.
* Eevee says `llvmpipe` on Windows: Docker Desktop passes CUDA through WSL 2, but not always
  the NVIDIA EGL driver. Eevee then runs in software; use Cycles on the GPU instead, which is
  the fast path there.

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
