# 2D Character Generation in Blender: Plan

Status: draft for M's review, 2026-11-05 (updated the same day with M's GPU and the shared bake-off brief). This is a plan only. Nothing in it has been built.

Companion document: the 3D asset generation requirements (`asset-generation/REQUIREMENTS.md`
in the project files). That document covers turning pictures into 3D meshes; this one covers
making characters that *look* 2D. They share the same hardware tiers, the same local, free
image models and the same licence rules, and this plan refers to its sections rather than
repeating them.

The document is written for someone new to animation. Words in *italics* the first time they
appear are explained in the glossary (section 14).

---

## 1. The short answer

There are two ways to get polished, 2D-looking, Pixar-flavoured characters out of Blender:

| | Route A: 3D rendered as 2D ("toon shading") | Route B: true 2D ("cutout" characters) |
|---|---|---|
| What it is | Keep today's 3D characters, rigs, outfits, scenes and animations. Change only how they are *drawn*: flat colour bands, outlines, soft rim light. | Characters are flat pictures cut into parts (head, torso, arms, legs, eyes, mouths), painted by a local image model, pinned to a 2D skeleton and moved like a paper puppet. |
| How it looks | Like a modern TV cartoon made in 3D (think "cel-shaded" games and series). Clean, consistent from every angle. As good as the 3D model underneath, no better. | Like an illustrated storybook or a 2D web series. The art can be far richer than today's stand-in mesh, because a picture model paints it. |
| Movement | Everything the studio does today: walks, mocap, camera pushes and pans, full 3D sets. | Puppet-style: limbs rotate, mouths and eyes swap. Turning around needs separate drawings for each view. |
| What breaks | Nearly nothing. | Outfits, mocap retargeting, most of the 3D animation library, free camera moves around a character. |
| Effort | About 2 to 3 weeks. | About 10 to 14 weeks for the whole pipeline; 1 to 2 weeks for a first one-character test. |

**Recommendation: build Route A now, and run a small, time-boxed Route B test in parallel to
decide whether true 2D is worth the bigger change.** Route A is a small change on top of work
that already exists (the GPU rendering branch `claude/project-thread-n48hco`), keeps every
phase that has been accepted so far, and gives a visible "2D" look within weeks. Route B is
the only way to get hand-illustrated art, but it replaces large parts of the studio, so it
should earn its place with one character in one shot first (section 11, phase B0).

Because character look is the pipeline's main constraint, the final choice is made by a
**common bake-off** (section 12): one original girl, Zara, walking in and saying one line in the living room,
rendered at 640x360 by every route, including the AI-generated 3D mesh route from
REQUIREMENTS (same brief as its section 9.1), and scored blind by M.

One honest note on "Pixar style": Pixar's look is 3D (soft light, rounded forms, big
expressive eyes). In 2D it means "illustrated with soft shading and appealing proportions".
Route A gets closer to it on motion and lighting; Route B gets closer on surface detail. Also,
the studio should never put "Pixar" in prompts, file names or published metadata, or copy any
of Pixar's characters; prompts describe the look in generic words ("soft 3D animated film
style, rounded shapes, big expressive eyes") (risk R8).

---

## 2. Goals and non-goals

### 2.1 Goals

1. **G1. A 2D look for every studio character.** Any character in the library can be rendered
   with a flat, outlined, cartoon look, without being rebuilt.
2. **G2. Consistency.** A character looks like the same character in every pose, angle,
   expression and shot of an episode.
3. **G3. Lip sync and expressions keep working.** The existing Rhubarb mouth cues (10
   *visemes*) and the 9 expressions of the rig profile drive the 2D character too.
4. **G4. Local and free.** Everything runs on M's Windows PC with its NVIDIA GPU. No paid API
   keys, no cloud services (same rule as REQUIREMENTS G4).
5. **G5. Fits the studio.** Results are versioned, checked and published like every other
   asset, and episodes render through the existing render and episode stages.
6. **G6. Novice-friendly.** M can make a new character from a short description and a few
   button presses, and can see and approve each step.

### 2.2 Non-goals

- Hand-drawn, frame-by-frame animation (every frame drawn by an artist). Both routes move a
  rig; nobody draws in-betweens.
- Training a brand-new image model. Small add-on training (a character *LoRA*) is allowed
  in Route B.
- Matching a specific studio's or film's look, or reproducing any existing character.
- Replacing the 3D pipeline in this plan. Route B, if adopted, runs beside it as a second kind
  of character.

---

## 3. What the studio has today (the starting point)

Everything below lives on branch `v2-42-phase8-acceptance` and was accepted in phases 1 to 8.

| Stage | Today | Key code |
|---|---|---|
| Character | A procedural stand-in humanoid with a Rigify rig (~400 bones), 43 face shape keys, drivers from the face control bone `face_ctrl`. Built from a `CharacterSpecification` resolved against the `family_cartoon@1.0` style profile. | `blender_engine/cartoon_engine/character/`, `contracts/studio_contracts/character.py`, `style_profile.py`, `rig_profile.py` |
| Outfits and accessories | 3D garments fitted to the body (fit space, verification), accessories attached to bones. | `outfit/`, `accessory/` |
| Animation | Generated actions (walk, wave, talk, happy…) and BVH mocap retargeted onto Rigify. | `animation/` |
| Environments and props | 3D sets with lighting and camera marks; props placed on them. | `environment/` |
| Scene | Instances of characters placed in a set, with clips, a face track (expressions, blinks, lip sync keys on `face_ctrl` custom properties `expr_*`, `eye_blink_*`, `vis_*`), camera moves (`push_in`, `pull_out`, `pan_left`, `pan_right`). | `scene/assemble.py`, `scene/face.py`, `scene/camera_moves.py` |
| Lip sync | Rhubarb finds mouth cues in recorded audio; `scene/face.py` keys the matching `vis_*` property. Visemes: `AI, E, O, U, FV, L, MBP, WQ, TH, rest`. | `scene/face.py`, `assets/audio.py` |
| Render and episode | Per-shot render (Cycles on CPU by default, Eevee when a GPU is present), H.264 assembly into `episode.mp4`. Series settings hold `RenderSettings` (engine, samples, colour management). | `jobs/render_scene.py`, `contracts/studio_contracts/episode.py` |
| GPU | Branch `claude/project-thread-n48hco` adds NVIDIA detection, Eevee on GPU and Cycles on CUDA/OptiX (`core/device.py`, `gpu_check.py`, `infra/compose.gpu.yaml`). Not yet merged. | |

The single most useful fact for this plan: **every face movement in a scene is a number on a
custom property of `face_ctrl`.** Anything that reads those numbers (3D shape keys today, or
swapped 2D mouth pictures tomorrow) gets lip sync and expressions for free, with no change to
`scene/face.py`.

---

## 4. Route A: 3D rendered as 2D (toon shading)

### 4.1 The idea in plain words

A normal render works out how light falls smoothly over a surface. A *toon shader* takes that
smooth result and snaps it into two or three flat bands (lit, shadow, deep shadow), the way a
cartoonist paints a shadow shape instead of a gradient. Add a dark *outline* around the
silhouette and along big folds, a thin bright *rim light* on the edge facing away from the
light, and switch off realistic colour tone mapping, and a 3D character reads as 2D.

### 4.2 Blender features used

| Feature | What it does here | Notes |
|---|---|---|
| **Eevee** (Blender's real-time engine) | The renderer for this look. Fast on the GPU: seconds per frame instead of minutes. | Needs the GPU branch; Eevee cannot run on the CPU-only workers (render_scene already refuses with a clear reason). |
| **Shader to RGB** node | Turns the light that reached a surface into a plain colour that can then be banded. | Eevee only. This is why Route A needs Eevee and cannot use Cycles. |
| **Color Ramp** (constant interpolation) | Snaps the light into 2 or 3 flat bands; the band edges and shadow tint come from the style profile. | Slight softness (a "linear" ramp with a narrow gap) reads more Pixar-like than hard bands. |
| **Layer Weight / Fresnel** node | Rim light on silhouette edges. | |
| **Inverted hull outline** (Solidify modifier with flipped normals and a black, back-face-culled material) | A clean outline around every character and garment. Cheap, renders on the GPU at Eevee speed, moves with the rig. | Recommended for characters, outfits and accessories. Width scales with distance unless corrected; a driver from camera distance keeps it steady. |
| **Freestyle** | Line drawing as a post pass: creases, silhouettes, material borders. | Better lines but runs on the CPU and is slow on long shots. Optional for environments and stills. |
| **Grease Pencil Line Art modifier** | Generates real Grease Pencil strokes from 3D geometry; lines can wobble like ink. | Best-looking lines, heaviest to compute. Optional "premium" outline mode, measured in phase A2. |
| **Custom normals for faces** (Data Transfer from a sphere onto the head, or a Normal Edit modifier) | Stops ugly shadow blobs on noses and cheeks, the most common toon-shading problem. Shadows on the face fall like a painted sphere. | Done once per template, so every character from it benefits. |
| **Colour management "Standard"** | Keeps flat colours flat (Filmic and AgX wash them out). | Already the series default. |
| **World and lights** | One key light (sun) for the band direction, flat ambient fill so shadows are tinted, not black. | Environments get the same toon material treatment so characters do not look pasted on. |

No third-party add-ons are needed. Everything above ships with Blender 5.2.3 and can be set
up from `bpy`, which is how the studio builds everything.

### 4.3 Where it plugs into the pipeline

**The look is applied at scene assembly and render time, not baked into published
characters.** Published versions are immutable, and there are already published characters,
outfits and sets. If toon materials were baked into `character.blend`, every asset would need
republishing and the series could not switch looks. Instead:

1. `scene/assemble.py` builds the scene as today.
2. A new step, `look/apply.py`, runs when the series' render settings say `shading: "toon"`:
   * swaps each character, outfit, accessory and environment material for a toon material
     built from the original's base colour (the colours come from the resolved specification,
     so nothing has to be guessed from pictures),
   * adds the inverted hull outline modifiers,
   * applies face custom normals (shipped with the template, see 4.2),
   * sets the key light direction and ambient fill from the toon settings.
3. `jobs/render_scene.py` renders with Eevee on the GPU.

Nothing in the library, the animation stage, lip sync or the episode stage changes.

### 4.4 Data model changes

Small and additive.

```python
# contracts/studio_contracts/episode.py
class ToonSettings(Model):
    bands: Literal[2, 3] = 2
    band_edges: tuple[float, ...] = (0.45,)          # where light turns into shadow (0..1)
    band_softness: float = Field(default=0.04, ge=0, le=0.3)
    shadow_tint: str = Field(default="#6A5ACD", pattern=HEX)   # shadows lean blue-violet, not grey
    shadow_strength: float = Field(default=0.35, ge=0, le=1)
    rim: float = Field(default=0.25, ge=0, le=1)
    outline: Literal["hull", "freestyle", "lineart", "none"] = "hull"
    outline_px: float = Field(default=2.5, ge=0, le=12)        # at 1080p
    outline_colour: Literal["black", "darkened_base"] = "darkened_base"

class RenderSettings(Model):
    ...
    shading: Literal["standard", "toon"] = "standard"
    toon: ToonSettings = Field(default_factory=ToonSettings)
    # validator: shading == "toon" requires engine == "eevee"
```

* Episodes freeze their series render settings as they do today, so an episode rendered in
  toon renders the same way again.
* The style profile gains nothing in phase A1. If per-character tweaks are wanted later
  (a character whose outline is thicker), they go in an optional `look` block on the
  character specification, resolved once in code like everything else.
* The template gains a `head_normals` custom-normal source mesh (a new template version).

### 4.5 Consistency across poses

Automatic. It is the same 3D model and rig in every frame, so proportions, colours and
features cannot drift. The only thing to watch is outline width changing with distance from
camera, handled by the camera-distance driver.

### 4.6 What breaks

* **Cycles-only machines.** Toon needs Eevee, so it needs the GPU workers. The CPU fallback
  is a standard Cycles render; a series set to toon fails clearly on a CPU worker rather than
  silently changing look.
* **Very thin or overlapping parts** (glasses, fingers) can get messy outlines. The hull width
  is reduced per material for those, and accessories can opt out.
* **The look is only as appealing as the mesh.** The stand-in humanoid is simple. Toon shading
  makes it cleaner and more cartoon-like, but does not make it Pixar-quality. Better template
  meshes (an artist's, or a generated base from the asset-generation prototype, REQUIREMENTS
  G3 and section 7.2) are what raise that ceiling.

### 4.7 Effort for Route A

| Piece | Size |
|---|---|
| Merge or rebase the GPU branch; confirm Eevee renders on M's PC | 1 to 2 days |
| Toon material builder, ramp and rim from `ToonSettings` | 3 to 4 days |
| Inverted hull outlines with distance correction; per-material opt-out | 2 to 3 days |
| Face custom normals in the template (new template version) | 2 to 3 days |
| Environment and prop toon pass | 2 days |
| Contracts, validation, web form switch on series settings | 1 to 2 days |
| Acceptance run (re-render the phase 8 episode in toon), demo record | 2 days |
| **Total** | **about 2 to 3 weeks** |

---

## 5. Route B: true 2D cutout characters

### 5.1 The idea in plain words

A *cutout* character is a paper puppet. Its art is a stack of flat pictures with transparent
backgrounds: head, hair, torso, upper arms, forearms, hands, thighs, shins, feet, plus a set
of eyes and a set of mouths. Each picture is placed on a flat card in Blender, and the cards
are pinned to a simple 2D skeleton (an *armature*). Rotating the upper-arm bone swings the
upper-arm card and everything below it. Talking swaps which mouth card is visible. A character
seen from the side is a second puppet with its own drawings.

Route B has two halves: **making the art** (a local image model paints the parts) and
**rigging and animating it in Blender**.

### 5.2 Making the art

Run locally with **ComfyUI** (free, GPL-3, has a Windows portable build that uses the NVIDIA
GPU directly, and a node graph M can look at). The studio drives it through ComfyUI's local
HTTP API with saved workflow files, so M presses one button and the same steps run every
time. Models and their VRAM needs follow REQUIREMENTS sections 3.2 and 4.2.

| Step | Tool | What happens |
|---|---|---|
| B-1 Concept | **SDXL** in ComfyUI's low-VRAM mode (fits M's 8 GB card; FLUX.1 [schnell] only after a GPU upgrade, section 8), plus a cartoon / animated-film style LoRA | From the character's description, make 4 to 8 candidate full-body pictures. M picks one. The seed and every setting are recorded. |
| B-2 Character sheet | Same model, **ControlNet** (OpenPose for pose, Lineart or Canny for shape) and **IP-Adapter** (keeps the face and colours of the chosen picture) | Paint the picked character again in a fixed *rigging pose*: front view, arms slightly away from the body, legs apart, mouth closed, eyes open, plain background. Then the 3/4, side and back views from the same reference (the "turnaround"). |
| B-3 Character LoRA (optional, recommended for main cast) | **kohya_ss** or **ai-toolkit** on the PC (several hours and only just fits on M's 8 GB card, section 8; 1 to 2 hours on a 16 GB card) | Train a small add-on from the approved sheet and 15 to 30 accepted variations, so later pictures of this character (new outfits, new views, new expressions) come out recognisably the same. |
| B-4 Background removal | **BiRefNet** (MIT), as in REQUIREMENTS 4.3 | Clean transparent cut-out of each view. |
| B-5 Cut into parts | **SAM 2** (Apache 2.0) for masks, guided by an OpenPose skeleton so each mask knows which limb it is | Each view becomes separate part images. |
| B-6 Fill hidden areas | **Inpainting** with the same model and LoRA (or LaMa for small fills) | Where the arm covered the torso, paint the missing torso, so the torso card is whole when the arm swings away. Joints get overlap "caps" (a rounded end on the upper arm) so no gap shows when a limb rotates. |
| B-7 Eyes and brows | Inpainting at the eye region, fixed seed and LoRA | Eye states: open, half, closed (blink), wide, squint; brow states per expression. |
| B-8 Mouths | Inpainting at the mouth region | One mouth per viseme (10) per view, plus expression mouths (smile, frown, open laugh…). Alternatively, see 5.5: vector mouths drawn with Grease Pencil, which are more consistent than painted ones. |
| B-9 Review sheet | Pillow (image library) | One picture showing every part, every mouth and every eye state side by side for M to approve before rigging. |

The layered result is stored as PNG files plus a small JSON description (5.6). PSD files are
not needed, but the importer could accept a PSD that an artist layered by hand, which gives a
manual way in if the AI cut is not good enough.

### 5.3 How the character stays consistent

This is the hardest problem in AI-made 2D characters, and the plan attacks it from four sides:

1. **Draw once, move many times.** The puppet's parts are painted once. Every pose, walk and
   gesture in every shot moves *the same pictures*, so a pose cannot change the face or the
   colours. Image models are only used at character creation, never per frame. This is the
   main reason cutout is the right 2D technique for an AI pipeline: the AI's inconsistency is
   confined to a step a person approves.
2. **Same reference for every view.** All views and every mouth and eye are painted from the
   approved front picture with IP-Adapter, ControlNet and a fixed seed, and with the
   character LoRA once it exists.
3. **Masked inpainting for small parts.** Mouths and eyes are painted *inside* the existing
   face picture, so the skin tone, line weight and lighting match the face around them.
4. **Checks with numbers, then a person.** An automatic check compares each view's colours
   against the front view's palette (colour distance per region), and part outlines against
   the skeleton (no missing limb, no stray pixels). M approves the review sheet; nothing
   publishes without that.

### 5.4 Rigging in Blender

| Feature | Use |
|---|---|
| **Image planes** (Blender's built-in "Image as Planes" import, available from `bpy`) | One card per part, with an emission (unlit) material and alpha, so the painted art shows exactly as painted. |
| **Armature** (a simple 2D one, not Rigify) | A fixed bone set, the new `cutout_humanoid_v1` rig profile: root, hips, spine, chest, neck, head, and for each side upper arm, forearm, hand, thigh, shin, foot. Each card is parented to one bone, so it moves rigidly (no bending pixels). |
| **Pivot points** | Each part's rotation point (shoulder, elbow…) comes from the OpenPose joints found in B-5, so the arm swings from the shoulder, not its middle. |
| **Draw order** | Cards sit a few millimetres apart in depth so the far arm is behind the torso; a `z_order` per part and per view controls it. A driver can reorder cards for poses where the near arm crosses the body. |
| **Mesh deform for soft parts** (optional) | Cards for the torso and hair can be subdivided and weighted to two bones, so they bend slightly instead of hinging. |
| **Face control bone `face_ctrl`** | Same name and same custom properties (`expr_*`, `vis_*`, `eye_blink_*`) as the 3D rig profile. Drivers read them to pick which mouth, eye and brow card is visible (5.5). |
| **View switch** | Each view (front, 3/4, side, back) is its own set of cards on the same armature, in its own collection. A custom property `view` on the root bone shows one set. Left-facing views are mirrored right-facing ones where the character is symmetric. |
| **COA Tools 2** (community add-on, "Cutout Animation Tools") | Good for M to look at and tweak a rig by hand. The studio's builder does not depend on it, because add-ons can lag behind Blender versions (check it supports 5.2 before installing). The studio writes its own small importer in `bpy` that reads `parts.json`. |
| **Grease Pencil** (Blender's 2D drawing system, v3 in Blender 5.x) | Optional for vector mouths, eyebrows and outlines drawn by code (5.5); also for effects like motion lines and sweat drops. |

### 5.5 Mouths, eyes and expressions driven by the existing face track

`scene/face.py` already keys, for every frame, a number between 0 and 1 on each `vis_*`,
`expr_*` and `eye_blink_*` property of `face_ctrl`. A 2D character reads the same numbers:

* **Mouths.** For each view there are 10 viseme cards. A driver on each card's material alpha
  (or its visibility) shows the card whose `vis_*` value is highest. Cut-out mouths should
  *snap* between shapes, not cross-fade, so the driver thresholds at 0.5. The existing half
  frame hand-over in `face.py` gives a clean snap.
* **Expressions.** Each of the 9 expressions has a brow card, an eye variant and an "idle
  mouth" card. When an `expr_*` value passes 0.5 its cards show; while a character speaks,
  the viseme mouth wins over the expression mouth, and the expression is kept in brows and
  eyes (that is how 2D animators do it).
* **Blinks.** `eye_blink_L/R` picks open, half or closed eye cards (0 to 0.33 to 0.66 to 1).
* **Two mouth options, decided in phase B0:**
  1. *Painted mouths* (inpainted, section 5.2 step B-8): match the art best, can drift
     between visemes.
  2. *Vector mouths*: a Grease Pencil mouth drawn by code from a few numbers per viseme
     (width, open height, lip curve, teeth, tongue) in the character's lip colour. Perfectly
     consistent and cheap to add views to; slightly less painterly. Likely the safer default.

No change to Rhubarb, audio clips or the face track is needed. The rig profile is the
contract, and the 2D rig honours it.

### 5.6 Data model and format changes

A new asset kind, `character_2d`, beside `character`. It goes through the same generic asset
pattern (draft, build, validate, publish, immutable versions; architecture 3.2 and 6).

**Published version folder:**

| File | What it is |
|---|---|
| `character.blend` | Image-plane cards, the `cutout_humanoid_v1` armature, `face_ctrl`, drivers, view collections. Written by `bpy`, like every studio `.blend`. |
| `parts/<view>/<part>.png` | The painted parts with transparency, one folder per view. |
| `face/<view>/mouth_<viseme>.png`, `eyes_<state>.png`, `brow_<expression>.png` | Face sets (or `face/mouths.json` for vector mouths). |
| `parts.json` | For every part: view, file, bone, pivot (pixels), `z_order`, size in metres. |
| `character.json` | The `Character2DSpecification` (below) plus the generation record. |
| `manifest.json` | Every file with checksum, rig profile and style profile versions, as today. |
| `validation.json` | The checker's report. |
| `thumbnail.png`, `pose_sheet.png`, `face_sheet.png`, `turn.mp4` | Pictures for people: the poses, all mouths and eyes, and a quick view-switch preview. |

**Specification (contracts, sketch):**

```python
class Character2DSpecification(Model):
    schema_version: Literal["character_2d/1"] = "character_2d/1"
    name: str
    description: str                       # what M typed, kept for regeneration
    style_profile: Ref                     # a 2D style profile, e.g. "storybook_2d@1.0"
    rig_profile: Ref = Ref(id="cutout_humanoid", version="1")
    views: tuple[Literal["front", "three_quarter", "side", "back"], ...] = ("front", "three_quarter", "side")
    mouths: Literal["painted", "vector"] = "vector"
    height_m: float                        # so it stands the right size next to 3D props and sets
    palette: dict[str, str]                # skin, hair, eyes, top, bottom, shoes: hex colours, checked against the art
    generation: Generation                 # model, LoRA(s) with licences, seeds, ControlNet/IP-Adapter settings, workflow file checksum
```

`Generation` records every model and licence the same way as REQUIREMENTS 7.3
(`generators[]`), so a licence decision later can find every affected character.

**Rig profile:** `cutout_humanoid_v1` lists bones, views, part names, and **reuses the same
`expressions`, `visemes`, `eye_keys` and `face_control_bone` as `CARTOON_HUMANOID_V1`**. A new
style profile kind for 2D describes the art vocabulary (line weight, shading softness, prompt
fragments and negative prompts) instead of 3D proportions.

**Database:** one new migration adding the `character_2d` asset type and its spec column,
following the pattern of migrations 0005 to 0012.

### 5.7 How the other stages adapt

| Stage | What changes for 2D characters |
|---|---|
| **Library** | New "2D characters" gallery and page: description form, candidate picker (B-1), review sheet approval (B-9), versions and publish as today. |
| **Outfits** | The 3D outfit system (garments fitted to a body) does not apply. A 2D outfit is a **replacement part set**: new torso, arm and leg pictures for the same character, inpainted with the character LoRA so the face and body match. It becomes an `outfit_2d` asset tied to *one* character (not shareable across bodies like 3D outfits). This is the biggest loss in Route B. |
| **Accessories** | Workable: a hat, glasses or watch is a small card parented to a bone (`head`, `hand.L`) with a per-view picture. Painted once per view. |
| **Animation** | Rigify actions and mocap retargeting do not transfer. A new set of generated 2D actions for `cutout_humanoid_v1` (idle, walk, wave, talk gestures, happy bounce, point, nod) written like the existing `animation/generators.py`, per view. Mocap can be flattened onto a side-view plane later (optional, risky). |
| **Scenes and environments** | Two options, chosen in phase B0: **(1) 2.5D in the existing 3D sets.** Each 2D character is a set of cards standing in the 3D room, turned to face the camera, picking the view (front, 3/4, side) from its angle to the camera. Keeps sets, props and camera marks; needs the sets toon-shaded (Route A's environment pass) so the flat characters do not look pasted on. **(2) 2D backgrounds.** Painted background and foreground layers (generated with the same image model and style), placed at different depths for *parallax*. Closest to classic 2D; 3D sets are not used. Option 1 is recommended because it reuses phase 7's sets. |
| **Camera moves** | `push_in`, `pull_out`, `pan_left` and `pan_right` all work in 2.5D (the cards are flat, and these moves stay roughly in front of them). Orbiting around a character, or high and low angles, do not: a flat card seen from the side disappears. Scene checks reject camera angles more than about 35 degrees off the card's facing. |
| **Lip sync and face** | Unchanged (5.5). |
| **Render** | Eevee with unlit emission materials; very fast (often under a second a frame on GPU). Optional Grease Pencil effects. `jobs/render_scene.py` needs only the device handling from the GPU branch. |
| **Episode** | Unchanged: shots render to frames and are assembled into `episode.mp4` as today. A shot may mix 3D (toon) and 2D characters only if the series uses Route A's toon look, otherwise the styles clash. |

### 5.8 What breaks, all in one place

* Shareable outfits (become per-character replacement parts).
* Mocap and the Rigify animation library (replaced by 2D generated actions).
* Free camera moves around characters (limited to in-front angles).
* Characters turning smoothly (they switch between 3 to 4 drawn views; animators "cheat" this
  with a quick blink or motion during the switch).
* Fit and collision checks built for 3D bodies (replaced by simpler 2D checks: parts present,
  pivots inside parts, no gaps at joints in the test poses).

### 5.9 Effort for Route B

| Piece | Size |
|---|---|
| B0 spike: one character, front view only, rigged by a script, one 10-second talking shot | 1 to 2 weeks |
| ComfyUI set-up on M's PC, saved workflows, API client, generation record | 1 week |
| Turnaround views, part cutting, inpainting, review sheet | 2 to 3 weeks |
| Character LoRA training flow | 1 week |
| `character_2d` contracts, migration, library API and pages | 1 to 2 weeks |
| Cutout rig builder, view switch, face drivers, checks | 2 weeks |
| 2D animation generators (idle, walk, wave, talk, happy, point, nod) per view | 2 weeks |
| Scene support (2.5D cards in sets, view picking, camera-angle checks) | 1 to 2 weeks |
| 2D outfits and accessories | 1 to 2 weeks |
| Acceptance and demo record | 1 week |
| **Total after the spike** | **about 10 to 14 weeks** |

---

## 6. Comparison and recommendation

| Question | Route A (toon) | Route B (cutout) |
|---|---|---|
| Looks 2D? | Yes, "3D cartoon drawn flat". | Yes, truly illustrated. |
| Art quality ceiling | The 3D mesh. Today's stand-in is simple. | The image model. Can be much richer now. |
| Consistency | Perfect, automatic. | Good with the steps in 5.3, but needs review per character. |
| Keeps outfits, mocap, sets, camera moves | All of them. | Few of them. |
| New Blender skills needed | Shader nodes, modifiers. | Cutout rigging, drivers, views, Grease Pencil. |
| Risk | Low. | Medium to high. |
| Time | 2 to 3 weeks. | 1 to 2 week spike, then 10 to 14 weeks. |

**Recommendation.** Character look is the pipeline's main constraint, so the routes are not
chosen on paper: they are compared in one common bake-off (section 12), together with the
AI-generated 3D mesh route from REQUIREMENTS. Do Route A first (phases A0 to A3). Run Route B's spike (B0) alongside it,
on its own branch, with a clear question: *does one AI-made cutout character, talking for 10
seconds, look good enough that M would trade outfits and free camera moves for it?* If yes,
continue with B1 onward as a second character kind; the toon-shaded sets from Route A become
the backgrounds for 2D characters (section 5.7, 2.5D option), so Route A's work is not wasted
either way. If no, the asset-generation prototype's character bases (REQUIREMENTS G3) are the
better way to raise quality, because they improve the mesh Route A draws.

---

## 7. Pipeline stages at a glance

**Route A**

```
character / outfit / set (published, unchanged)
   └─► assemble scene ─► [new] apply toon look ─► Eevee render (GPU) ─► frames ─► episode.mp4
                              ▲
                     series RenderSettings.shading = "toon", ToonSettings
```

**Route B**

```
description ─► [B-1 concepts] ─► M picks ─► [B-2 turnaround sheet] ─► [B-3 LoRA, optional]
   ─► [B-4 background removal] ─► [B-5 cut parts] ─► [B-6 fill hidden] ─► [B-7/8 eyes, mouths]
   ─► [B-9 review sheet] ─► M approves ─► [build cutout rig in Blender] ─► [checks] ─► publish character_2d
                                                                                       │
scene (2.5D cards in a toon set) ◄── 2D actions + face track (unchanged) ◄─────────────┘
   └─► Eevee render (GPU) ─► frames ─► episode.mp4
```

Each Route B step writes its own files, so a step can be rerun (a better mouth set) without
repeating earlier ones, as in REQUIREMENTS section 5.

---

## 8. Hardware

**M's PC: NVIDIA GeForce RTX 3060 Ti with 8 GB of VRAM** (checked with `nvidia-smi` inside
Docker on 2026-11-05, so the GPU already works in containers). The Windows desktop uses about
1.8 GB of it, which leaves **about 6 GB free** for the studio. That is **tier A** in
REQUIREMENTS 3.2.

| Tool | VRAM it needs | Fits on the 3060 Ti? | Plan |
|---|---|---|---|
| **Eevee** (Route A and all renders) | 1 to 3 GB at 640x360 and 1080p for these scenes | Yes, comfortably | Route A is not limited by this card. |
| **SDXL** (Route B art, the bake-off reference picture) | 8 GB normally, about 4 to 6 GB with ComfyUI's low-VRAM mode (parts of the model wait in system RAM) | **Yes, with low-VRAM mode**, about 30 to 60 s per 1024 px picture | The image model for Route B. |
| SDXL + ControlNet + IP-Adapter together (step B-2) | adds about 1 to 2.5 GB | Tight | Use the smaller "Control-LoRA" ControlNets, one guide at a time, and low-VRAM mode. Measured in B0. |
| **FLUX.1 [schnell]** | about 12 GB in 8-bit | **No** (offloaded versions are far too slow) | Out for now. Every FLUX mention in this plan applies only after a GPU upgrade. |
| BiRefNet, SAM 2, OpenPose, LaMa | under 2 GB each | Yes | Run one at a time. |
| SDXL character LoRA training (B-3) | 8 GB+ even with memory-saving settings | Only just: batch size 1, gradient checkpointing, cached latents, nothing else open; several hours per character | Optional. Try once in B1; if it fails, rely on IP-Adapter and inpainting for consistency, or train on a rented or upgraded GPU later. |

What this means in practice:

* **Route A is unaffected** by the card's size.
* **Route B works, but slowly and at SDXL quality.** One model is loaded at a time and ComfyUI
  runs in low-VRAM mode. Generating a full character (concepts, three views, parts, 10 mouths,
  eye and brow sets) is likely an hour or two of machine time rather than minutes. Vector
  mouths (5.5) avoid much of that.
* **Never run ComfyUI and a Blender render at the same time**; each wants most of the free
  VRAM.
* System RAM matters for low-VRAM mode: 32 GB is recommended (REQUIREMENTS 3.3 and question 5).
* ComfyUI runs natively on Windows (portable build), which is simpler for M than Docker. The
  Blender side still runs in the studio's worker containers.
* If the bake-off shows the look is held back by the card, a 16 GB card (tier C) unlocks
  FLUX.1 [schnell], comfortable LoRA training, and the better 3D models in REQUIREMENTS, so
  one upgrade helps every route.

---

## 9. Acceptance criteria

### Route A

| # | Criterion |
|---|---|
| AA1 | The phase 8 demo episode re-renders with `shading: "toon"` on M's PC with no change to any published asset (library before and after are identical, as in earlier phase records). |
| AA2 | Every character, outfit, accessory, prop and set surface in the episode shows 2 or 3 flat bands (checked by counting distinct luminance levels per material ID in a test frame) and an outline of the set width within ±1 px at 1080p. |
| AA3 | No shadow blobs on faces: the face of each character in the 9 expressions sheet shows one clean shadow shape (checked by a person on the sheet, and by the band count on the face region). |
| AA4 | Lip sync and expressions behave exactly as in the standard render (same `face_ctrl` keys, verified by the existing reopen reports). |
| AA5 | Render time per 1080p frame on M's GPU is under 2 seconds for the demo shots. |
| AA6 | A series set to toon on a CPU-only worker fails with a clear "toon needs Eevee on a GPU" message. |
| AA7 | Rendering the same shot twice gives identical frames (the studio's determinism rule). |

### Route B

| # | Criterion |
|---|---|
| AB1 (spike) | From a one-line description, one character is generated, rigged by script and talks for 10 seconds to a Rhubarb-synced line, rendered by the studio's render job. M judges it "good enough to pursue". |
| AB2 | All generation runs locally and offline after model download; the generation record names every model, LoRA, seed and licence. |
| AB3 | Consistency: in the review sheet, each view's region colours are within a set colour distance of the front view's palette, and M approves the sheet before rigging. |
| AB4 | No gaps or overlaps show at any joint across a pose test (each limb rotated through its allowed range), checked automatically on rendered alpha. |
| AB5 | All 10 visemes and 9 expressions show the right cards when driven from `face_ctrl` (automatic check renders each and compares against the expected card). |
| AB6 | The 2D character walks, waves and talks in front, 3/4 and side views, and switches view correctly when its facing angle to the camera changes. |
| AB7 | A 2.5D scene with a 2D character in a phase 7 set renders with push-in and pan moves; an orbiting camera is rejected by the scene check with a clear reason. |
| AB8 | Publishing, versioning and immutability behave exactly as for 3D characters (the existing immutability tests extended to `character_2d`). |

---

## 10. Risks

| # | Risk | Route | Mitigation |
|---|---|---|---|
| R1 | The GPU branch is not merged, or Eevee fails on M's machine or in Docker on WSL 2. | A, B | Phase A0 is exactly this check. Eevee also runs in Blender on Windows directly as a fallback for testing. |
| R2 | Toon shading makes the simple stand-in look *more* obviously simple. | A | Face normals and soft bands help; real quality gains come from better template meshes (REQUIREMENTS G3). Set expectations with an early test frame in A1. |
| R3 | AI art is inconsistent between views, mouths or outfits. | B | Section 5.3: draw once, IP-Adapter and ControlNet, masked inpainting, character LoRA, numeric checks, M's approval. Vector mouths as the consistent default. |
| R4 | Automatic part cutting (SAM 2) makes ragged or wrong masks, especially hair and hands. | B | Rigging pose with limbs apart; OpenPose guidance; manual PSD import as an escape hatch. |
| R5 | The cutout look feels stiff ("paper puppet"). | B | Mesh deform on torso and hair, secondary motion (hair bounce driven by head motion), squash and stretch on the root, and good 2D action generators. Judge in B0. |
| R6 | Outfits per character is a big step back from shareable 3D outfits. | B | Accept for main cast; reuse inpainting with the character LoRA to make outfits quicker. |
| R7 | Image model or LoRA licences forbid commercial use (many community LoRAs do; FLUX.1 [dev] is non-commercial). | B | Only SDXL (Open RAIL++-M) and FLUX.1 [schnell] (Apache 2.0) bases; only LoRAs whose licence allows commercial use, recorded per character; prefer training the studio's own style LoRA from approved art. Same rule as REQUIREMENTS 4.4. |
| R8 | Style imitation and trademark: prompts or outputs that name or copy a real studio's look or characters. | A, B | Generic style words only; no studio or film names in prompts, files or metadata; a prompt filter in the generation step. |
| R9 | COA Tools 2 or other add-ons lag behind Blender 5.2. | B | The pipeline does not depend on them; the studio's own `bpy` importer builds the rig. |
| R10 | Mixed 2D and 3D characters in one shot clash. | B | Only allowed when the series uses the toon look; the scene check enforces it. |

---

## 11. Phased plan

Each phase ends with something M can watch.

| Phase | What | Done when | Rough size |
|---|---|---|---|
| **A0 GPU check** | Rebase or merge `claude/project-thread-n48hco`; run `gpu_check`; render one demo shot with Eevee on M's PC. | Eevee shot renders on the GPU. | 1 to 2 days |
| **A1 Toon characters** | Toon material, bands, rim, inverted hull outline, face normals, `ToonSettings` contract. | One character turntable and the expressions sheet in toon; AA3. | 1 to 1.5 weeks |
| **A2 Toon scenes** | Outfits, accessories, props and sets in toon; outline mode comparison (hull, Freestyle, Line Art) with timings. | AA2, AA5, AA7 on a phase 7 scene. | 4 to 5 days |
| **A3 Toon episode** | Series switch in the web app; re-render the phase 8 episode; demo record. | AA1, AA4, AA6. | 2 to 3 days |
| **B0 Spike** (in parallel with A1 to A3, own branch) | ComfyUI on M's PC; one character, front view; parts cut semi-manually if needed; scripted cutout rig; vector mouths vs painted mouths; one 10-second talking shot. | AB1, and a go / no-go from M. | 1 to 2 weeks |
| *Decision point: bake-off* | Section 12: R0, R1, R2, R3 and R3t compared on one clip; M picks the route(s) to continue. | Scores recorded. | 2 days, plus waiting on R3 |
| **B1 Art pipeline** | Saved workflows, turnaround, part cutting, inpainting, review sheet, generation record. | AB2, AB3. | 3 to 4 weeks |
| **B2 Asset kind** | `character_2d` contracts, migration, API, library pages, publish. | AB8. | 1 to 2 weeks |
| **B3 Rig and face** | Rig builder with views, face drivers, checks. | AB4, AB5. | 2 weeks |
| **B4 Animation** | 2D action generators per view. | AB6. | 2 weeks |
| **B5 Scenes** | 2.5D cards in toon sets, view picking, camera checks, 2D accessories and outfits. | AB7. | 2 to 3 weeks |
| **B6 Episode** | A short episode with 2D characters; demo record. | All AB criteria. | 1 week |

---

## 12. Common bake-off test

Character look is the main constraint of the whole pipeline, so the candidate solutions are
judged side by side on one small, identical test, not on paper. Every route makes **the same
girl character** and renders **the same short clip in the same scene from the same script**.

### 12.1 The contenders

| ID | Route | Where it is described |
|---|---|---|
| **R0 Baseline** | Today's stand-in humanoid, standard render. Shows the starting point. | Section 3 |
| **R1 Toon 3D** | Route A: the stand-in rendered with the toon look. | Section 4 |
| **R2 Cutout 2D** | Route B: AI-painted parts on a cutout rig, 2.5D in the set. | Section 5 |
| **R3 AI 3D mesh** | A character base mesh made by the asset-generation prototype (REQUIREMENTS G3, phase P6), rigged with Rigify by the studio, rendered standard. | REQUIREMENTS 6 and 7.2 |
| **R3t AI 3D mesh, toon** | R3 with Route A's toon look. Cheap to add once R1 and R3 exist, and likely the strongest 3D combination. | Sections 4 and REQUIREMENTS |

### 12.2 Fixed inputs (identical for every contender)

This is the same brief as REQUIREMENTS section 9.1, so the AI 3D mesh route and both routes
in this plan make exactly the same clip. If the two documents ever disagree, 9.1 wins and this
section is updated to match.

| Item | Fixed value (identical for every contender) |
|---|---|
| Character | **Zara**, an original girl about 8 years old, in a soft, rounded, feature-film 3D cartoon style (a "Pixar-like" look, but an original design; generic style words only, R8). Proposed written description: *"Zara, a cheerful 8-year-old girl, big expressive brown eyes, warm brown skin, curly black hair in a high ponytail with a yellow hair tie, yellow t-shirt, blue denim overalls, red sneakers. Soft, rounded animated-film style."* The same description and **the same front reference picture** (made once with SDXL at a recorded seed, section 8) go to every route. |
| Action | She walks in from the left, about four steps, stops facing the camera, and says one line. Automatic blinks on; no other gestures. |
| Line | *"Hi! I'm Zara, and today we're going to the park!"* **One audio file**, shared by every route, lip-synced once with Rhubarb so every contender gets the same mouth cues. |
| Length | About 6 seconds, 24 fps. |
| Scene | The living room environment (`ENV-001`), camera mark `wide` (no camera move), lighting preset warm indoor. Toon-shaded for R1, R2 and R3t, standard for R0 and R3. R2 stands its cutout cards in the same 3D room (2.5D, section 5.7); if that is not ready yet, it uses a still render of the room from `wide` as its background, as 9.1 allows. |
| Output | 640 × 360 MP4 with the audio (H.264), same colour management. Eevee on M's GPU for every contender (Cycles for R0 and R3 only if Eevee standard looks markedly worse; record which). |
| Machine | M's PC, one contender at a time, ComfyUI and other GPU apps closed while rendering. |

The scene, script and render settings are one episode manifest; each contender only swaps the
character reference (and the series `shading` setting), so the comparison is as fair as the
pipeline allows.

### 12.3 Known unequal terms (stated up front)

* **Walking:** R0, R1, R3 and R3t use the studio's walk generator on Rigify. R2 uses the new
  2D walk for the side and 3/4 views. Same timing and stride length.
* **Lip sync on R3/R3t:** an AI-generated mesh has no face shape keys. For the bake-off its
  mouth and eyes are **decals**: Route B's vector mouths and eye cards projected onto the face
  and driven by the same `face_ctrl` properties. That is a real candidate in its own right
  (many stylised productions do faces this way), and is noted as such in the results.
* **R2 turns:** the walk-in uses the side view and switches to front at the stop. That switch
  is part of what is being judged.
* **R2 background:** 2.5D in the room, or a still render of it (see the table above). The
  report says which was used.

### 12.4 What each contender delivers

A folder `bakeoff/<ID>/` with `clip.mp4` (640x360), `still_close.png` (the last frame of the
push-in), `face_sheet.png` (9 expressions and 10 visemes), `turn.png` (front, 3/4, side, back
where the route supports it), and `report.json`: tools and model versions, licences, seeds,
hands-on time to make the character, machine time to make it, render seconds per frame, VRAM
peak, and anything done by hand.

### 12.5 How they are judged

1. **Blind look test by M.** The five clips play side by side, labelled only A to E in random
   order, looping. M scores each 1 to 5 on: *appeal* (do you like looking at her?), *"film
   look"* (closest to the soft animated-film look you want), *face and lip sync* (does she seem
   to talk?), *motion* (the walk and stop feel alive), and *consistency* (same girl in every frame
   and on the turn sheet).
2. **Measured facts** from `report.json`: hands-on minutes per new character, machine minutes
   per new character, render seconds per frame, what breaks in the rest of the pipeline
   (outfits, mocap, camera moves; from sections 4.6 and 5.8).
3. **Decision rule.** The winner is the highest summed look score, unless it is within 2 points
   of a contender that keeps more of the pipeline working, in which case the cheaper route wins.
   M can overrule; the scores and clips are kept in the demo record either way.

### 12.6 When it runs and what it costs

The bake-off replaces the separate "go / no-go" at the end of B0 and needs:

* R0: available now (render the manifest).
* R1: end of phase A1 (toon characters) plus the toon set from A2.
* R2: the B0 spike, extended by the 2D walk for one view (about 3 extra days).
* R3/R3t: REQUIREMENTS phases P0, P1, P2 and P6 for a character base (the slow part), plus
  about 3 to 4 days in the studio to rig it with Rigify and add face decals.
* About 2 days to write the shared manifest, the comparison page (clips side by side, blind
  labels, score form) and the report.

Realistically the full bake-off is ready **4 to 6 weeks** from the start, with R0, R1 and R2
comparable after about 3 weeks while R3 catches up. Phase order in section 11 stays the same,
with the bake-off as the decision point after A3 and B0.

---

## 13. Open questions for M

| # | Question | Default if no answer |
|---|---|---|
| 1 | ~~Which GPU and how much VRAM?~~ Answered: RTX 3060 Ti, 8 GB (section 8). How much system RAM does the PC have? (Low-VRAM mode leans on it.) | 32 GB assumed. |
| 2 | Which look do you picture: "3D cartoon drawn flat" (Route A) or "illustrated storybook" (Route B)? A couple of reference pictures you like would settle it. | Route A first, Route B spike to compare. |
| 3 | Will the cartoons be sold or monetised? (Licences, R7.) | Treat as commercial. |
| 4 | For 2D, painted mouths or cleaner vector mouths? | Vector, decided properly in B0. |
| 5 | For 2D, keep the 3D sets (2.5D) or switch to painted backgrounds? | Keep the 3D sets, toon-shaded. |

---

## 14. Glossary

* **Armature:** Blender's skeleton object. Bones move the parts attached to them.
* **ComfyUI:** a free program for running image models locally as a graph of steps; it can be
  driven from code through a local web API.
* **ControlNet:** an add-on to an image model that makes it follow a guide picture, such as a
  stick-figure pose (OpenPose) or line drawing.
* **Cutout animation:** animating a character made of separate flat pieces, like a paper puppet.
* **Driver:** a small rule in Blender that sets one value from another (for example "show
  this mouth when `vis_O` is above 0.5").
* **Eevee:** Blender's fast real-time render engine (runs on the GPU).
* **Grease Pencil:** Blender's system for 2D drawings and strokes inside a 3D scene.
* **Inpainting:** asking an image model to repaint only a masked part of a picture, keeping the
  rest.
* **Inverted hull:** an outline trick: a slightly larger, inside-out copy of a mesh painted
  black, so only its edge shows around the original.
* **IP-Adapter:** an add-on to an image model that copies the look of a reference picture
  (face, colours, style) into new pictures.
* **LoRA:** a small add-on file (tens of MB) that teaches an image model a style or a
  character without retraining the whole model.
* **Outline:** the dark line around a cartoon shape.
* **Parallax:** near layers moving faster than far layers when the camera moves, which gives
  flat pictures a sense of depth.
* **Rim light:** a thin bright edge on the side of a shape away from the main light.
* **Toon shader:** a material that paints light in a few flat bands instead of smooth shading.
* **Viseme:** the mouth shape for a group of speech sounds (for example M, B and P all close the
  lips: `MBP`).
* **2.5D:** flat 2D characters or pictures placed at different depths in a 3D scene.
