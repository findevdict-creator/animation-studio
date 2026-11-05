# 3D Asset Generation Prototype: Requirements

Status: draft for M's review, 2026-11-05. Nothing in this document has been built yet.

This is a separate prototype (an MVP) that does one job: turn a picture or a short text
description into a 3D model, using free open-source AI models running on M's own Windows PC.
It starts with **props** (a chair, a lamp, a toy) and then moves on to **character base
meshes**. Its output is a file the Animation Studio can import later, then rig, check and
publish with the machinery it already has.

The document is written for someone new to 3D. Words in *italics* the first time they appear
are explained in the glossary at the end (section 13).

---

## 1. Your question first: what format are characters, and can we write them directly?

### 1.1 What the studio uses today

A published character in the studio is a folder of files (architecture section 4.3):

| File | What it is |
|---|---|
| `character.blend` | The real character. A Blender file holding the body *mesh*, the Rigify *rig* (about 400 bones), 43 face *shape keys*, *drivers* that connect face controls to shape keys, materials and textures. |
| `character.json` | The recipe: the character specification (age, body type, face shape, colours) the file was built from. |
| `manifest.json` | A list of every file with its checksum, plus the exact versions of the template, rig profile and style profile used. |
| `preview.glb` | A lightweight copy for the web browser viewer. It has the deform bones and face shapes but none of Blender's rig controls. |
| `validation.json` | The report from the checker that had to pass before publishing. |
| `thumbnail.png`, `turntable.mp4`, `expressions.png` | Pictures for people. |

Props are similar: `prop.blend` plus a report, a thumbnail and `preview.glb`.

All of these `.blend` files are written by Python code running *inside* Blender (the `bpy`
module), never by hand and never by another program.

### 1.2 Could we write `.blend` files ourselves by learning the format?

Not in a useful way. A `.blend` file is close to a raw copy of Blender's working memory at the
moment it was saved. It carries a description of Blender's internal structures (called
"SDNA") so Blender can read it back, but there is no public specification, the structures
change between Blender versions, and the meaning of the data (how a Rigify rig works, how a
driver is evaluated) lives in Blender's code, not in the file. Programs that read `.blend`
files from outside Blender only manage simple things like mesh positions. Writing one that
Blender would open correctly, with a working rig, is far harder than asking Blender to write
it.

The studio already does the sensible version of "learning the data structure": it uses
Blender's own Python API, which is the official, stable way to create anything a `.blend`
can hold. That stays.

### 1.3 What about OpenUSD, glTF and the other open formats?

These are *interchange formats*: open, documented formats made for moving 3D data between
programs. Blender imports all of them.

| Format | Good at | Weak at | Fit for this prototype |
|---|---|---|---|
| **glTF / GLB** (`.glb` is the one-file version) | One object or a small scene with meshes, *PBR* materials, textures, skeletons and morph targets. Every AI 3D model listed below outputs it. Browsers display it (the studio's viewer already uses `preview.glb`). Blender's importer is built in and reliable. | No notion of layers or overrides; no Blender-specific rig controls or drivers. | **Recommended output.** |
| **OpenUSD** (`.usd`, `.usda`, `.usdc`, `.usdz`) | Big pipelines: layering, overrides and references across thousands of assets (Pixar, NVIDIA Omniverse, Apple). Fully documented, with a Python library (`usd-core`) that can author files directly. | Heavy for a single mesh. Material round trips into Blender are less predictable than glTF. None of the AI models output it natively, so we would convert anyway. Does not carry Rigify controls or drivers either. | Optional second export later, if the studio ever moves scene interchange to USD. |
| **FBX** | The traditional game-engine format; carries rigs and animation. | Closed format owned by Autodesk; Blender's support is reverse-engineered. | No. |
| **OBJ** | Very simple: mesh plus a basic material file. | No PBR materials, no bones, no scale or up-axis information. | Only as a debug export. |

**The recommendation:** the prototype writes **GLB** files. When the studio wants an asset,
a Blender job in the studio imports the GLB with `bpy` and saves the studio's own `.blend`
from it, then runs the existing checks. The prototype never writes `.blend` files and never
needs to know about rigs, drivers or the studio's database.

**The trade-off in one line:** USD is the more powerful pipeline format, but for "one
generated object handed to Blender" GLB is simpler, is what the AI models produce anyway,
and is already the format the studio's viewer reads. Choosing GLB now does not prevent a USD
export later; Blender can write USD from the same imported object.

**What no format solves:** a character's *rig*, face controls and drivers are Blender
constructs. Whatever format the mesh arrives in, the studio still has to build the rig
inside Blender. That is what the studio's character engine already does (section 7).

### 1.4 Can we use Unity or Unreal Engine instead?

They work well alongside this, but they do not replace it.

- **Game engines display and animate assets; they do not create them.** Unity and Unreal are
  where finished models are placed, lit, animated and played in real time. Neither one turns
  a picture or a sentence into a 3D model. That job is what the open AI models in section 4
  do, so the prototype is needed either way.
- **The prototype's output opens in both.** Unreal imports GLB/glTF and USD directly (USD
  through its built-in USD importer). Unity imports glTF through the free glTFast package and
  USD through its USD package. Choosing GLB (section 1.3) keeps every door open: the same
  `mesh.glb` goes into Blender, Unity, Unreal or a web page without conversion.
- **Rigging stays in Blender.** The studio's rig standard (Rigify bones, face shape keys,
  drivers) and its checks are built in Blender. Unity and Unreal can play animations on a
  skeleton, but they would not run the studio's character builder or validator.
- **A possible later role for Unreal: the final renderer.** Unreal renders in real time with
  very good lighting, so it could one day render finished episodes much faster than Blender's
  Cycles. In that setup Blender stays the core where assets are made, rigged and checked, and
  each scene is exported (as USD or FBX with its animation) to Unreal for the final pictures.
  That is a studio decision for later (architecture Q8, where rendering runs) and is out of
  scope for this prototype.

---

## 2. Goals and non-goals

### 2.1 Goals

1. **G1. Props from a picture.** Given one image of an object, produce a clean, textured,
   correctly sized 3D prop as a GLB file, on M's PC, in a few minutes.
2. **G2. Props from text.** Given a sentence ("a red cartoon toy fire truck"), first create
   a reference picture with a local text-to-image model, then do G1.
3. **G3. Character base meshes.** Given a front picture of a cartoon character standing in an
   A-pose, produce a body mesh that the studio could use as the starting shape of a
   *template family* (section 7.2), and prove it can be rigged and posed.
4. **G4. Local and free.** Everything runs on M's machine. No paid API keys, no cloud GPUs.
   After the one-time model downloads, generation works with the internet unplugged.
5. **G5. Studio-ready output.** Every asset is written to the output contract in section 7,
   so a future studio import job can use it without guessing sizes, orientation or origin.
6. **G6. Compare models fairly.** Several open models can be plugged in, run on the same
   test inputs, and compared side by side, so we pick the best one for M's GPU with evidence.

### 2.2 Non-goals (not in this prototype)

- Writing `.blend` files, or touching the studio's database, API, web app or storage.
- Rigging, face shape keys, expressions, visemes or outfits for characters. The prototype
  only proves a character mesh *can* be rigged with a quick automatic test (section 6.7).
- Animation, scenes, rendering of episodes.
- Training or fine-tuning AI models.
- Multi-user use, logins, a job queue, or running on a server.
- Professional, hand-made quality. The aim is "good enough to use as a prop in the
  background of a cartoon scene" and "a good starting shape for an artist", not finished
  hero art.
- Paid or hosted services (Meshy, Tripo, Rodin, Hunyuan3D 2.5/3.0 cloud, and so on).

---

## 3. Hardware and software requirements

### 3.1 What M has

Windows PC with an NVIDIA GPU, Docker Desktop using WSL 2. **Still needed: the GPU model and
its VRAM** (Task Manager, Performance, GPU, "Dedicated GPU memory"). The tiers below make the
document useful before we know.

### 3.2 VRAM tiers

*VRAM* (the memory on the graphics card) decides which models can run at all. Speed depends
mostly on the GPU generation. Figures are from the model authors' published requirements and
are approximate; the prototype measures them for real in Phase 1.

| Tier | VRAM | Example cards | What it can run well | What it cannot |
|---|---|---|---|---|
| **A** | 8 GB | RTX 3060 Ti, 3070, 4060, 4060 Ti 8 GB, 5060 | TripoSR, Stable Fast 3D, Hunyuan3D-2mini (shape only). Text-to-image with SDXL in low-memory mode. Texturing done in Blender instead of by the AI model. | TRELLIS, Hunyuan3D texture painting, InstantMesh. |
| **B** | 12 GB | RTX 3060 12 GB, 3080 12 GB, 4070, 4070 Super, 5070 | Everything in A, plus Hunyuan3D-2 shape (full size), TripoSG, FLUX.1-schnell in 8-bit. Hunyuan3D-2 texture painting only with memory offloading (slow). | TRELLIS officially (16 GB). |
| **C** | 16 GB | RTX 4060 Ti 16 GB, 5060 Ti 16 GB, 4070 Ti Super, 4080, 5080 | Everything in B, plus TRELLIS, Hunyuan3D-2 shape **and** texture, InstantMesh. This is the comfortable tier. | Hunyuan3D 2.1 texturing without offloading. |
| **D** | 24 GB+ | RTX 3090, 4090, 5090 | Everything, including Hunyuan3D 2.1 with PBR textures, and running text-to-image and image-to-3D back to back without unloading. | — |

Rule for the prototype: **only one AI model is loaded on the GPU at a time.** Each stage
loads its model, runs, and frees the memory before the next stage. This is what lets tiers A
and B work at all.

### 3.3 The rest of the PC

| Item | Minimum | Recommended | Why |
|---|---|---|---|
| Windows | Windows 10 22H2 | Windows 11 | GPU support in WSL 2. |
| NVIDIA driver | A current Game Ready or Studio driver installed on Windows (not inside WSL) | Latest | Docker containers use the Windows driver through WSL. |
| Docker Desktop | WSL 2 backend, GPU support working | Latest | Check with `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi`. |
| System RAM | 16 GB | 32 GB or more | Models are loaded into RAM before the GPU; offloading uses RAM. WSL takes half of RAM by default; set `memory=` in `%UserProfile%\.wslconfig` if needed. |
| Free disk (SSD) | 60 GB | 150 GB | Each model is 2 to 20 GB; container images 8 to 15 GB each; generated files are small. |
| CPU | Any 6-core | 8 cores or more | Mesh cleanup and texture baking in Blender run on the CPU. |
| Internet | Only for the first download of images and model weights | | After that, generation is offline (G4). |

Blender itself does **not** need to be installed on Windows. The cleanup stage runs Blender
headless inside a Linux container (the studio already has a Blender 5.2.3 worker image that
can be reused). Installing Blender on Windows is still handy for looking at results.

### 3.4 Why Docker and not plain Windows Python

Most of these models need custom GPU code compiled for Linux (for example `nvdiffrast`,
`spconv`, `kaolin`, and Hunyuan3D's texture rasteriser). Building these on Windows is the most
common reason people fail to run them. Linux containers under WSL 2 avoid that, and each model
gets its own container so their conflicting Python and PyTorch versions never meet.

---

## 4. Candidate models compared

### 4.1 Image-to-3D (the core stage)

| Model | Who | Licence | VRAM (approx.) | Input | Output | Speed on a modern GPU | Quality notes |
|---|---|---|---|---|---|---|---|
| **TripoSR** | Stability AI and Tripo | MIT | ~6 GB | 1 image | Mesh with vertex colours, no UVs | Under 1 s | Fast and robust but blobby; weak on thin parts and backs. A good baseline and tier A fallback. |
| **Stable Fast 3D (SF3D)** | Stability AI | Stability AI Community Licence: free for individuals and organisations under US$1M yearly revenue | ~6 to 7 GB | 1 image | UV-unwrapped textured mesh, simple material values | About 1 s | Cleaner than TripoSR, already has UVs. Best tier A choice. |
| **Hunyuan3D-2mini** | Tencent | Tencent Hunyuan 3D Community Licence (see 4.4) | ~5 GB shape | 1 image | Untextured mesh | Seconds | Small version of Hunyuan3D-2 shape; good shapes for its size. |
| **Hunyuan3D-2 (2.0)** | Tencent | Tencent Hunyuan 3D Community Licence | ~6 GB shape, ~16 GB shape plus texture | 1 image (also a multi-view variant, 2mv) | Detailed mesh; texture painting stage adds a UV texture | Shape 10 to 30 s; texture about 1 min | Among the best open shapes. Texture stage is the hardest part to install. |
| **Hunyuan3D 2.1** | Tencent | Tencent Hunyuan 3D Community Licence | ~10 GB shape, ~21 GB texture | 1 image | Mesh plus PBR textures (colour, roughness, metal) | About 1 to 2 min | Best open PBR texturing. Tier D, or tier C using shape only. |
| **TRELLIS** (image-large, also text variants) | Microsoft | MIT | 16 GB minimum (official) | 1 image, or several | GLB with UV texture, also Gaussian splats | 10 to 30 s | Very good, well-structured shapes and textures; handles hard-surface props well. Tier C+. Newer TRELLIS releases may need more VRAM; check before adding. |
| **TripoSG** | VAST | MIT | ~8 GB (shape) | 1 image | High-detail untextured mesh | Seconds | Sharp shapes; needs texturing elsewhere. Tier B candidate. |
| **InstantMesh** | Tencent ARC | Apache 2.0 | ~16 GB | 1 image (makes 6 views first) | Mesh with vertex colours or texture | 10 to 20 s | Older; mainly a comparison point. |

### 4.2 Text-to-image (optional first stage, for G2)

| Model | Licence | VRAM | Notes |
|---|---|---|---|
| **SDXL 1.0** (plus a cartoon style *LoRA* if wanted) | CreativeML Open RAIL++-M (commercial use allowed with use restrictions) | 8 GB with low-memory settings | The safe default on every tier. |
| **FLUX.1 [schnell]** | Apache 2.0 | ~12 GB in 8-bit, 24 GB full | Better at following prompts. Tier B and up. |
| FLUX.1 [dev] | Non-commercial licence | Same | **Avoid** unless the project stays non-commercial. |

### 4.3 Helper tools (no AI licence worries)

| Tool | Licence | Used for |
|---|---|---|
| **BiRefNet** or **rembg** | MIT | Removing the background from the input picture (the 3D models expect a single object on a plain background). |
| **Blender 5.2.3** (`bpy`, headless) | GPL | Cleanup, remeshing, decimation, UV unwrapping, texture baking, scale and orientation, renders. Same version the studio pins. |
| **trimesh** | MIT | Quick checks (watertight, bounds, triangle count) outside Blender. |
| **xatlas** | MIT | UV unwrapping if Blender's own Smart UV Project is not good enough. |
| **Gradio** | Apache 2.0 | The small web form (section 8). Most of the models already ship Gradio demos. |

### 4.4 Licences to check before using an asset commercially

- **Tencent Hunyuan 3D Community Licence** does not grant rights in the **European Union, the
  United Kingdom or South Korea**, and needs a separate licence above 1 million monthly
  active users. If M is in, or will sell into, those regions, Hunyuan models are out and
  TRELLIS / TripoSG / SF3D are the picks.
- **Stability AI Community Licence** (SF3D) is free below US$1M annual revenue; above that a
  paid licence is needed.
- **MIT and Apache 2.0** (TRELLIS, TripoSR, TripoSG, InstantMesh, FLUX.1 schnell) allow
  commercial use.
- The prototype records which model and licence produced every asset (section 7.3), so a
  licence decision later can find every affected file.

This is a summary, not legal advice; each model's licence file is the authority.

### 4.5 Starting picks by tier (to be confirmed by the Phase 3 comparison)

| Tier | Default shape model | Texture route | Text-to-image |
|---|---|---|---|
| A (8 GB) | Stable Fast 3D (fallback TripoSR) | SF3D's own texture, re-baked in Blender | SDXL, low memory |
| B (12 GB) | Hunyuan3D-2 shape or TripoSG | Project the input picture onto the mesh in Blender, or SF3D texture transfer | SDXL or FLUX.1 schnell 8-bit |
| C (16 GB) | TRELLIS | TRELLIS's own texture (or Hunyuan3D-2 paint) | FLUX.1 schnell 8-bit |
| D (24 GB+) | TRELLIS or Hunyuan3D 2.1 | Hunyuan3D 2.1 PBR | FLUX.1 schnell |

---

## 5. The pipeline at a glance

```
  text prompt ──► [1 reference image] ──┐
                                          ├─► [2 background removal] ─► [3 image-to-3D] ─► raw.glb
  your picture ──────────────────────────┘
                                                                         │
     mesh.glb ◄─ [7 checks + export] ◄─ [6 texture] ◄─ [5 normalise] ◄─ [4 cleanup]
```

Each numbered stage is a separate step with its own files on disk, so a stage can be rerun
without redoing the ones before it, and a failure says which stage failed.

---

## 6. Pipeline stages in detail

### 6.1 Stage 1: reference image (optional, text input only)

- Input: a prompt and a style preset. The default preset matches the studio's
  `family_cartoon` style: "single object, centred, plain light grey background, soft even
  lighting, three-quarter view from slightly above, cartoon style, simple shapes, no text".
- Produces 4 candidate images (fixed *seed* per candidate); the user picks one in the web
  form, or the CLI takes the first.
- For characters the preset adds "full body, front view, A-pose, arms 30 to 45 degrees from
  the body, feet apart, neutral expression, no props".
- Output: `01_reference/candidate_N.png`, `01_reference/chosen.png`.

### 6.2 Stage 2: background removal

- BiRefNet (or rembg) cuts the object out; the result is centred and padded to a square with
  the object filling about 85 % of the frame, which is what the 3D models expect.
- Rejects the image with a clear message if nothing, or more than one large object, is found.
- Output: `02_cutout/input.png` (with transparency).

### 6.3 Stage 3: image-to-3D

- Runs the chosen model in its own container with a fixed seed.
- Saves the model's untouched output so later stages can be improved without regenerating.
- Output: `03_raw/raw.glb`, `03_raw/run.json` (model, version, seed, settings, time taken,
  peak VRAM).

### 6.4 Stage 4: cleanup (Blender, headless)

AI meshes are usually a heavy, messy "triangle soup". This stage makes them usable:

1. Merge duplicate vertices; delete small floating fragments (under 1 % of the area).
2. Fix the surface: recalculate normals so they all face outwards; fill small holes. If the
   mesh is badly broken, a *voxel remesh* rebuilds it as one closed surface (with a setting
   for detail).
3. *Decimate* to a triangle budget: props 2,000 to 30,000 triangles depending on size class
   (small, medium, large); character base meshes 20,000 to 60,000.
4. Smooth shading with sharp edges kept above an angle (so a box looks like a box).

Output: `04_clean/clean.glb` and a list of what was changed.

### 6.5 Stage 5: normalise (size, orientation, origin)

This is what makes the asset plug into the studio without guesswork.

- **Up and front:** Z up and the front facing -Y inside Blender, which is the studio's
  convention (architecture 8.1 and 8.4). In the GLB file this appears as glTF's standard
  Y-up, and Blender's importer converts it back automatically.
- **Units and size:** metres, scale 1, all transforms applied. The real size comes from, in
  order: a height the user typed; the studio prop catalog size for a known category (a chair
  is 0.88 m tall, a table 0.72 m); a default per size class. Characters use the height the
  user gives, clamped to the style profile's range (a child is about 1.0 to 1.4 m).
- **Origin:** bottom centre of the object, sitting on the ground (Z = 0). Things that go on a
  table (books, apple) have the same rule, which matches the studio's `rests_on` idea.
- **Facing:** the image-to-3D models guess which way is "front" from the picture. The stage
  turns the object so the side that faced the camera faces -Y, and the web form lets the user
  rotate it in 90 degree steps if the guess is wrong.

Output: `05_normal/normal.glb`.

### 6.6 Stage 6: UVs and texture

The cleaned mesh no longer matches the model's original texture, so the colour is moved
across by *baking*:

- If the raw model had a UV texture (SF3D, TRELLIS, Hunyuan paint): unwrap the clean mesh
  (Smart UV Project or xatlas) and bake the colour from `raw.glb` onto it.
- If the raw model only had vertex colours (TripoSR, InstantMesh) or no colour (TripoSG,
  Hunyuan shape only): bake vertex colours, or project the input picture from the front and
  fill the back with the nearest colour. Projection is rough on the back; the report flags it.
- Texture size 1024 × 1024 for small props, 2048 × 2048 for large props and characters.
- Optional **toon flatten** setting: reduce the texture to a small palette of flat colours so
  the prop fits the studio's cartoon look.
- Materials are standard glTF PBR (colour texture, plus roughness and metal when the model
  gives them, otherwise roughness 0.6 and metal 0).

Output: `06_texture/textured.glb`, `06_texture/basecolor.png`.

### 6.7 Stage 7: checks and export

Writes the final files of section 7 and runs these checks (report, not a hard stop, except
the first two):

| Check | Pass when |
|---|---|
| file opens | Blender imports `mesh.glb` with no errors. |
| has geometry | One mesh object, at least 500 triangles. |
| triangle budget | Within the size class budget. |
| size | Bounding box matches the target size within 2 %. |
| grounded | Lowest point at Z = 0 within 1 mm; origin at bottom centre. |
| transforms applied | Location 0, rotation 0, scale 1. |
| normals | No inverted faces; fewer than 1 % non-manifold edges (watertight preferred, not required). |
| texture | UVs present, one material, texture within the size limit, no large black or empty areas. |
| floating parts | No separate piece smaller than 0.5 % of the total. |
| **character only:** pose | Roughly symmetric left to right (within 5 % of height); arms apart from the body. |
| **character only:** rig test | In a throwaway Blender file: place the Rigify human *metarig* at landmarks guessed from the mesh, generate the rig, bind with automatic weights, pose it in 4 test poses (arms up, step, sit, turn head) and render a contact sheet. Pass when binding succeeds and nothing tears visibly. This proves the mesh *can* become a template; it is not the studio's real rig. |

Renders four views (front, side, back, three-quarter) and a turntable GIF for review.

---

## 7. Output contract for the studio

### 7.1 Folder per asset

```
output/
  {asset_id}/                     e.g. prop-20261105-1432-toy-fire-truck
    mesh.glb                      the deliverable: clean, textured, normalised (section 6)
    asset.json                    what it is and how it was made (7.3)
    report.json                   the checks of 6.7 and their results
    thumbnail.png                 512 × 512, three-quarter view
    views.png                     front, side, back, three-quarter contact sheet
    turntable.gif
    source/
      prompt.txt                  if made from text
      input.png                   the picture used (after background removal)
      raw.glb                     the model's untouched output, for re-processing
    rig_test/                     characters only
      poses.png
      rig_test.json
    work/                         stage folders 01_reference … 06_texture (can be deleted)
```

### 7.2 How each kind plugs into the studio later

**Props.** A future studio job, `import_generated_prop`, runs in the existing Blender
worker: it imports `mesh.glb` with `bpy`, puts the object in a `PROP_` collection, stamps it,
saves `prop.blend`, and runs the existing prop checks (size, grounded, solid, surfaces). The
studio's prop specification today only describes catalog items built by code
(`PROPD-CHAIR` and so on), so it would gain a second source kind, "imported mesh", that points
at `mesh.glb` and its `asset.json`. The size, category, `solid` and `rests_on` values in
`asset.json` line up with the catalog's fields so the import is mechanical.

**Characters.** The studio's character engine is built so an artist-made template can replace
the procedural stand-in body (architecture 8.4: "An artist template replaces
`body.py`/`head.py`; the landmarks, rig fitting, drivers, validator and previews stay"). This
prototype's character mesh is a candidate for the **base of a template family**
(for example a new `TPL-HUMANOID-CHILD` body). Getting from the generated mesh to a real
template still needs work on the studio side that this prototype does not do:

- **Retopology:** AI meshes have random triangles. A template needs clean quad topology with
  edge loops at joints and around the eyes and mouth, so it bends well and can carry the 43
  face shape keys. This can be done by an artist, or semi-automatically (quad remeshing),
  and is the biggest open question (risk R3).
- **Separate parts:** AI characters come out as one fused shell (hair, clothes and eyes
  melted into the body). The studio needs separate eyes, a body without clothes (outfits are
  separate assets) and hair from the hair library.
- **Landmarks, weights, masks:** the studio fits Rigify to landmarks, transfers weights and
  needs `mask_<region>` vertex groups (architecture 8.3).

So the realistic promise for characters is: **the prototype gives a good starting shape in a
minute, and a proof that it rigs; turning it into a template is a separate studio task.** The
most useful character output is therefore a *body base* (A-pose, simple clothes or none, hair
as a cap shape) rather than a fully dressed character.

### 7.3 `asset.json`

```jsonc
{
  "schema_version": "generated_asset/1",
  "asset_id": "prop-20261105-1432-toy-fire-truck",
  "kind": "prop",                          // "prop" | "character_base"
  "name": "Toy fire truck",
  "category": "toy",                       // the studio's prop categories: furniture, household, toy, nature, vehicle, food
  "size_m": [0.31, 0.16, 0.14],            // width (x), depth (y), height (z) in metres
  "solid": false,                          // do characters collide with it (studio meaning)
  "rests_on": "floor",                     // "floor" | "top"
  "axes": { "up": "+Z", "front": "-Y", "units": "m", "origin": "bottom_centre" },  // as seen in Blender after import
  "mesh": { "file": "mesh.glb", "sha256": "…", "triangles": 8412, "watertight": true },
  "texture": { "resolution": 1024, "pbr": ["baseColor"], "toon_flattened": false },
  "source": { "type": "text", "prompt": "a red cartoon toy fire truck", "input_sha256": "…" },
  "generators": [
    { "stage": "reference_image", "model": "sdxl-base-1.0", "licence": "CreativeML-OpenRAIL++-M", "seed": 1234 },
    { "stage": "image_to_3d", "model": "trellis-image-large", "licence": "MIT", "seed": 42 },
    { "stage": "cleanup", "tool": "blender 5.2.3", "settings": { "target_triangles": 8000 } }
  ],
  "character": null,                       // for character_base: height_m, pose "A", symmetry error, rig_test result
  "checks_ok": true,
  "created_at": "2026-11-05T14:32:10Z",
  "prototype_version": "0.1.0"
}
```

---

## 8. User interface

Deliberately small. Two ways in, same pipeline underneath.

### 8.1 Command line

```
assetgen doctor                                   # checks GPU, VRAM, Docker, disk; says which tier you are
assetgen models list                              # which models are installed and fit your VRAM
assetgen models pull trellis                      # one-time download
assetgen prop  --image chair.png --height 0.9 --category furniture
assetgen prop  --text "a wooden rocking chair" --model sf3d --triangles 6000
assetgen character --image girl_front.png --height 1.25
assetgen redo  prop-20261105-1432-toy-fire-truck --from cleanup --triangles 4000
assetgen compare --inputs tests/props/ --models sf3d,trellis,hunyuan2 # side-by-side sheet
```

On Windows this is one command (`assetgen.cmd` or a PowerShell script) that starts the right
containers with Docker Compose.

### 8.2 Web form (Gradio, at http://localhost:7860)

One page:

1. Choose **Prop** or **Character base**.
2. Drop an image, or type a description and pick one of the 4 generated pictures.
3. Fill in name, category, real height (optional), triangle budget (slider with presets).
4. Pick a model (only the ones that fit the detected VRAM are shown).
5. **Generate.** A progress line shows each stage.
6. A 3D viewer shows the result; buttons to rotate the front by 90 degrees, rerun from
   cleanup with a different detail level, or toggle toon flatten.
7. The report's checks in a short list, and a link to open the output folder.

No accounts, no database. The output folder is the history.

---

## 9. Acceptance criteria

The prototype is done when all of these pass on M's PC (and are recorded in an
`acceptance.log` like the studio's phase demos):

1. **AC1 Setup.** `assetgen doctor` reports the GPU, VRAM and tier, and that Docker can see
   the GPU. A fresh install from the README takes under an hour (excluding download time).
2. **AC2 Offline.** With the network disconnected after `models pull`, every command below
   still works.
3. **AC3 Props from images.** A fixed test set of 10 prop pictures (chair, table, floor lamp,
   ball, toy car, apple, tree, bench, stack of books, teapot) each produces an asset whose
   checks pass, in under 5 minutes each on tier C (under 10 on tier A).
4. **AC4 Props from text.** The same 10 described in words produce assets the same way.
5. **AC5 Quality.** M rates each of the 20 props from a 4-view sheet as "usable in a
   background", "usable after a fix" or "unusable". At least 14 of 20 are "usable" and no
   more than 3 are "unusable".
6. **AC6 Contract.** Every output folder matches section 7: `mesh.glb` opens in Blender 5.2.3
   with Z up, front -Y, origin at the bottom centre, size within 2 % of `asset.json`.
7. **AC7 Studio import spike.** A 30-line `bpy` script (shipped with the prototype, run in the
   studio's Blender worker image) imports three generated props, saves each into a
   `prop.blend` with one `PROP_` collection, and the studio's existing `check_prop`
   measurements agree with `asset.json`. This proves the plug-in path without changing the
   studio.
8. **AC8 Characters.** Three character pictures (child, adult, stylised animal biped) each
   produce a `character_base` asset that passes the pose check and the rig test, with a pose
   contact sheet that M judges "bends without tearing".
9. **AC9 Comparison.** `assetgen compare` produces a side-by-side sheet and a table (time,
   peak VRAM, triangle count, check results) for at least two models on the 10 props, and the
   default model per tier in section 4.5 is updated from it.
10. **AC10 Repeatable.** Running the same input with the same model, seed and settings twice
    gives meshes whose triangle count and size match within 1 % (GPU maths is not perfectly
    deterministic, so exact bytes are not required).
11. **AC11 Traceable.** Every asset's `asset.json` names the model and licence for every
    stage.
12. **AC12 Fits the GPU.** No stage crashes with an out-of-memory error on the detected tier
    when using the models `assetgen models list` offers for it.

### 9.1 Common bake-off test (comparing look routes)

How a character looks is the main constraint of the whole studio, so this AI-generated
route will be compared against other ways of making characters: toon-shaded 3D (the
studio's own template route) and a true 2D cutout approach (planned in a separate
document). Every route makes **the same short clip**, so the comparison is fair.

**The test clip (identical for every route):**

| Item | Fixed value |
|---|---|
| Character | One girl, about 8 years old, in a soft, rounded, feature-film 3D cartoon style (a "Pixar-like" look, but an original design). The same written description and the same front reference picture are given to every route. |
| Action | She walks in from the left, about four steps, stops facing the camera, and says one line. |
| Line | "Hi! I'm Zara, and today we're going to the park!" One recorded audio file, shared by every route. |
| Length | About 6 seconds, 24 frames per second. |
| Scene | The studio's living room environment (`ENV-001`), same camera mark (`wide`), same lighting preset (warm indoor). A 2D route uses a still render of the same room as its background. |
| Output | 640 × 360 MP4 with the audio. |

**What this route has to do to make the clip.** The prototype gives a static character mesh
and a rig test (section 6.7). For the bake-off it also needs: the Rigify rig from the rig
test, a walk applied from the studio's walk animation, and a simple mouth (a jaw bone, or a
few mouth shapes made by hand) for the line, since generated meshes have no face shape keys
(section 7.2). That extra work is counted, because it is part of the real cost of this route.

**What is recorded for each route:**

1. The clip itself, side by side with the other routes.
2. M's rating of the look (appealing, consistent from every angle, fits a children's cartoon)
   and of the motion (walk, lip sync) on a 1 to 5 scale.
3. Hands-on time: hours of human fixing needed, separate from waiting time.
4. Machine time: generation and render time on M's PC.
5. Where it broke: the steps that needed a workaround, and whether they would repeat for
   every new character.

---

## 10. Risks

| # | Risk | Likelihood | Effect | What we do about it |
|---|---|---|---|---|
| R1 | Generated props look "AI-made" (lumpy, melted details) and clash with the clean cartoon style. | High | Props unusable in hero shots. | Toon-flatten option; target background props first; the comparison (AC9) picks the cleanest model; keep the procedural catalog for hero items. |
| R2 | Installing the models fails on Windows (custom GPU code). | Medium | Weeks lost. | Linux containers only; start with the easiest model (SF3D or TripoSR) so the pipeline works end to end before adding hard ones; pin every image version. |
| R3 | Character meshes are fused, triangulated and have no face topology, so they cannot become a studio template without retopology. | High | G3 only gives a starting shape. | Set expectations (7.2); measure with the rig test; decide later between an artist, semi-automatic quad remeshing, or fitting the studio's own template mesh onto the generated shape (shrink-wrap), which keeps the studio's clean topology. That last option is likely the best and is worth a studio-side spike. |
| R4 | M's GPU has less VRAM than the good models need. | Unknown until M answers | Lower quality. | Tiers, offloading, one model at a time; tier A route is still useful. |
| R5 | Licence limits (Hunyuan territory exclusions, SF3D revenue cap, FLUX dev non-commercial). | Medium | Assets cannot be used commercially. | Record licences per asset (AC11); prefer MIT/Apache defaults; confirm M's country and plans (question 2). |
| R6 | Backs and hidden sides are guessed badly from one picture. | High | Wrong-looking backs on props. | Multi-view inputs where the model supports them (Hunyuan3D-2mv, TRELLIS multi-image); the reference stage can generate a side or back view; props are usually seen from the front in scenes. |
| R7 | Thin parts (chair legs, lamp stems, fingers) vanish or merge. | High | Broken props. | Comparison picks models that keep them; voxel remesh resolution setting; report flags very thin bounding regions. |
| R8 | WSL 2 runs out of memory or disk. | Medium | Crashes that look unrelated. | `assetgen doctor` checks `.wslconfig` memory and free disk; documented fix. |
| R9 | The open-model landscape changes monthly. | Certain | Today's best model is old next quarter. | Each model is an adapter behind one interface (image in, GLB out); adding one is a new container and a small adapter, nothing else changes. |
| R10 | Generated assets resemble copyrighted designs (a prompt for a famous character). | Low to medium | Legal and reputational. | Prompts are recorded; the studio's review step before publishing stays the gate. |

---

## 11. Phased plan

Each phase ends with something M can run and look at.

| Phase | What | Done when | Rough size |
|---|---|---|---|
| **P0 Machine check** | Confirm the GPU works in Docker; `assetgen doctor`; decide the tier. | AC1 | 1 to 2 days |
| **P1 One model, raw output** | One container (SF3D on tier A/B, TRELLIS on C/D), background removal, CLI `assetgen prop --image`. Raw GLB only. | A prop picture becomes a `raw.glb` M can open. | 2 to 4 days |
| **P2 Clean, normalise, texture, check** | Blender stages 4 to 7, the output contract, the report and renders. | AC3, AC6, AC10, AC11 on the 10 props | 1 to 2 weeks |
| **P3 Compare models** | Add one or two more models as adapters; `assetgen compare`; update section 4.5 from the results. | AC9, AC12 | 1 week |
| **P4 Text and the web form** | Text-to-image stage, the Gradio page. | AC2, AC4, AC5 | 1 week |
| **P5 Studio import spike** | The small `bpy` import script against the studio's Blender worker image. | AC7 | 2 to 3 days |
| **P6 Character bases** | Character preset, pose check, Rigify rig test and pose sheet. | AC8 | 1 to 2 weeks |
| Later (studio, not this prototype) | `import_generated_prop` job and "imported mesh" prop source in the studio; template-from-generated-mesh spike (R3). | | |

---

## 12. Open questions for M

| # | Question | Default if no answer |
|---|---|---|
| 1 | Which GPU and how much VRAM? | Write for tier C, keep tier A working. |
| 2 | Which country are you in, and will the cartoons be sold or monetised? (Licences, 4.4.) | Treat as commercial and avoid Hunyuan and FLUX dev until answered. |
| 3 | Where should the prototype's code live: a folder in this repo (`prototypes/assetgen/`) or its own repo? | A folder in this repo on its own branch, so it can share the studio's contracts later. |
| 4 | For characters, a body base (no clothes, simple hair cap) or a fully dressed look? | Body base, because the studio keeps outfits and hair as separate assets. |
| 5 | How much RAM and free disk does the PC have? | 32 GB and 150 GB assumed. |

---

## 13. Glossary

- **Mesh:** the 3D surface of an object, made of many small flat faces (usually triangles or
  four-sided "quads") joined at points called vertices.
- **Topology:** how those faces are connected. Clean topology has tidy rows of quads following
  the shape, which is what makes a character bend nicely.
- **Retopology:** rebuilding a messy mesh with clean topology over the same shape.
- **UVs / UV unwrapping:** flattening the 3D surface onto a 2D square so an image (the
  texture) can be painted onto it, like unfolding a cardboard box.
- **Texture / baking:** a texture is the image wrapped onto the mesh. Baking copies colour
  from one mesh onto another mesh's texture, for example from the messy original to the
  cleaned one.
- **PBR material:** "physically based rendering": a material described by colour, roughness
  and metalness, which every modern 3D program understands the same way.
- **Vertex colours:** colours stored on the mesh points themselves instead of in an image.
  Simple but blurry.
- **Decimate:** reduce the number of faces while keeping the shape, so the file is lighter.
- **Voxel remesh:** rebuild the mesh by filling it with tiny cubes and wrapping a new surface
  around them; fixes holes and overlaps but loses sharp detail.
- **Watertight / non-manifold:** a watertight mesh is a closed surface with no holes.
  Non-manifold edges are places where the surface is broken or tangled.
- **Rig:** an invisible skeleton of bones inside a character, with controls, that animators
  move to pose it. **Rigify** is Blender's built-in rig generator the studio uses.
- **Metarig:** Rigify's simple starting skeleton, which you line up with the body before
  Rigify generates the full rig.
- **Weights:** how much each bone moves each part of the mesh.
- **Shape keys / morph targets:** stored alternative shapes of a mesh (a smile, a blink) that
  can be blended in. Blender calls them shape keys, glTF calls them morph targets.
- **Drivers:** small Blender rules that make one value follow another (move a face control,
  the smile shape key follows).
- **Template family:** in the studio, one hand-made base body (for example "child") that
  every character of that kind is shaped from, so they all share the same rig and fit the
  same outfits.
- **VRAM:** memory on the graphics card. AI models must fit in it to run fast.
- **Seed:** a number that fixes the randomness of an AI model, so the same seed and input give
  (nearly) the same result.
- **LoRA:** a small add-on file that teaches an image model a style.
- **Interchange format:** a documented file format made for moving 3D data between programs
  (glTF, USD, FBX, OBJ).
- **Headless:** running a program like Blender without opening its window, controlled by a
  script.
