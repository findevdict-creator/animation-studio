"""Human-readable exports from the package. No LLM call; the JSON stays the source of truth."""

from __future__ import annotations

import csv
import io

from studio.schemas import ProductionPackage


def screenplay_md(pkg: ProductionPackage) -> str:
    names = {c.id: c.name for c in pkg.characters}
    locs = {l.id: l.name for l in pkg.locations}
    out = [f"# {pkg.story.title}", "", f"_{pkg.story.logline}_", ""]
    for sc in pkg.scenes:
        setting = "INT." if next((l for l in pkg.locations if l.id == sc.location_id), None) and \
            next(l for l in pkg.locations if l.id == sc.location_id).setting_type == "interior" else "EXT."
        out.append(f"## {sc.order}. {setting} {locs.get(sc.location_id, sc.location_id).upper()} - {sc.time_of_day.upper()}")
        out.append("")
        if sc.summary:
            out.append(sc.summary)
            out.append("")
        for shot in sc.shots:
            d = shot.direction
            out.append(f"**{d.id}** ({d.shot_type}, {d.camera_movement}, {d.duration_sec:g}s)")
            out.append("")
            if d.action:
                out.append(d.action)
                out.append("")
            for line in d.dialogue:
                paren = f" ({line.delivery})" if line.delivery else ""
                out.append(f"**{names.get(line.speaker_id, line.speaker_id).upper()}**{paren}")
                out.append(f"{line.text}")
                out.append("")
    return "\n".join(out).rstrip() + "\n"


def shot_list_csv(pkg: ProductionPackage) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["scene", "shot", "beat", "duration_sec", "frames", "shot_type", "angle", "movement",
         "characters", "props", "lines", "action"]
    )
    for sc in pkg.scenes:
        for shot in sc.shots:
            d, l = shot.direction, shot.layout
            writer.writerow(
                [
                    sc.id, d.id, d.beat_id, f"{d.duration_sec:g}", l.frames if l else "",
                    d.shot_type, d.camera_angle, d.camera_movement,
                    " ".join(c.character_id for c in d.characters),
                    " ".join(d.prop_ids), len(d.dialogue), d.action,
                ]
            )
    return buf.getvalue()


def bible_md(pkg: ProductionPackage) -> str:
    out = [f"# {pkg.story.title}: bible", "", "## Characters", ""]
    for c in pkg.characters:
        out.append(f"### {c.name} (`{c.id}`, {c.role})")
        out.append("")
        if c.goal:
            out.append(f"- Goal: {c.goal}")
        if c.arc:
            out.append(f"- Arc: {c.arc}")
        if c.personality:
            out.append(f"- Personality: {', '.join(c.personality)}")
        a = c.appearance
        looks = ", ".join(x for x in (a.height_class, a.build, a.skin, a.hair, a.eyes) if x)
        if looks:
            out.append(f"- Appearance: {looks}")
        out.append(f"- Asset: {c.asset.status} ({c.asset.library_id or c.asset.base_id or c.asset.build_request_id})")
        out.append(f"- Outfits: {', '.join(o.id for o in c.outfits)}")
        if c.voice_notes:
            out.append(f"- Voice: {c.voice_notes}")
        out.append("")
    out += ["## Locations", ""]
    for l in pkg.locations:
        out.append(f"### {l.name} (`{l.id}`, {l.setting_type})")
        out.append("")
        if l.description:
            out.append(l.description)
        out.append(f"- Asset: {l.asset.status} ({l.asset.library_id or l.asset.kit_id or l.asset.build_request_id})")
        out.append(f"- Anchors: {', '.join(a.id for a in l.anchors)}")
        out.append("")
    if pkg.props:
        out += ["## Props", ""]
        for p in pkg.props:
            out.append(f"- **{p.name}** (`{p.id}`): {p.description}")
        out.append("")
    if pkg.build_requests:
        out += ["## Open build requests", ""]
        for br in pkg.build_requests:
            out.append(f"- `{br.id}` ({br.kind}, {br.status}) for `{br.for_id}`: {br.spec}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


EXPORTS = {
    "screenplay": ("screenplay.md", screenplay_md),
    "shot_list": ("shot_list.csv", shot_list_csv),
    "bible": ("bible.md", bible_md),
}
