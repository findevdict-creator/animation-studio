import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { controlFor } from "../components/GlbViewer";
import { VersionTimeline } from "../components/VersionTimeline";
import type { VersionSummary } from "../api/client";

afterEach(cleanup);

const v = (id: string, status: string, label: string | null, based?: string, decision?: string): VersionSummary => ({
  id, character_id: "c", status, version_label: label, based_on_version_id: based ?? null, spec_digest: "d",
  built_digest: "d", fit_profile: "child_average", height_m: 1.28, review_decision: decision ?? null,
  published_at: label ? "2026-11-04T10:00:00Z" : null, deprecated_at: null, created_at: "2026-11-04T09:00:00Z", created_by: null,
});

describe("controlFor", () => {
  it("groups the brow/eyes/mouth parts of an expression under one control", () => {
    expect(controlFor("expr_happy_mouth")).toBe("expr_happy");
    expect(controlFor("expr_surprised_brow")).toBe("expr_surprised");
    expect(controlFor("vis_AI")).toBe("vis_AI");
    expect(controlFor("blink_L")).toBe("blink_L");
  });
});

describe("VersionTimeline", () => {
  const versions = [v("d3", "draft", null, "v2", "reject"), v("v2", "published", "2.0", "v1"), v("v1", "deprecated", "1.0")];

  it("shows each version with status, lineage and the recommended one", () => {
    render(<VersionTimeline versions={versions} selected="v2" recommended="v2" onSelect={() => {}} />);
    const items = screen.getAllByRole("listitem");
    expect(items.map((li) => li.querySelector(".tl-label")?.textContent)).toEqual(["draft", "2.0", "1.0"]);
    expect(items[0].textContent).toContain("changes requested");
    expect(items[0].textContent).toContain("from 2.0");
    expect(items[1].textContent).toContain("recommended");
    expect(items[1].className).toBe("selected");
    expect(items[2].textContent).toContain("deprecated");
  });

  it("selects a version on click", () => {
    const onSelect = vi.fn();
    render(<VersionTimeline versions={versions} onSelect={onSelect} />);
    fireEvent.click(screen.getAllByRole("button")[2]);
    expect(onSelect).toHaveBeenCalledWith("v1");
  });
});
