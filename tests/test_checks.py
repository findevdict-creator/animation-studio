from studio import checks


def _errors(issues):
    return [i for i in issues if i.severity == "error"]


def test_fixture_story_passes_all_checks(bible, characters, world, direction, layout, manifest, config):
    assert _errors(checks.check_characters(bible, characters, manifest, config)) == []
    assert _errors(checks.check_world(bible, characters, world, manifest, config)) == []
    assert _errors(checks.check_direction(bible, characters, world, direction, config)) == []
    issues = checks.check_layout(characters, world, direction, layout, manifest, config)
    assert _errors(issues) == [], [i.message for i in issues]


def test_missing_sheet_for_cast_member(bible, characters, manifest, config):
    characters.characters.pop(1)
    issues = checks.check_characters(bible, characters, manifest, config)
    assert any("char_fox" in i.message and i.category == "missing_reference" for i in issues)
    assert all(i.owner_agent == "character_designer" for i in issues)


def test_unknown_library_id(bible, characters, manifest, config):
    characters.characters[0].asset.library_id = "lib_char_nobody"
    issues = checks.check_characters(bible, characters, manifest, config)
    assert any(i.category == "missing_asset" for i in issues)


def test_strict_mode_rejects_build_requests(bible, characters, manifest, config):
    from studio.schemas.build_requests import BuildRequest

    config.flags.library_mode = "strict"
    br = BuildRequest(id="br_char_fox", kind="character", for_id="char_fox", spec="A glowing fox.")
    characters.build_requests.append(br)
    characters.characters[1].asset.status = "build_request"
    characters.characters[1].asset.library_id = None
    characters.characters[1].asset.build_request_id = "br_char_fox"
    issues = checks.check_characters(bible, characters, manifest, config)
    assert any("strict" in i.message for i in issues)


def test_expression_uses_unknown_shape_key(bible, characters, manifest, config):
    characters.characters[0].expressions["angry"] = {"nostril_flare": 1.0}
    issues = checks.check_characters(bible, characters, manifest, config)
    assert any("nostril_flare" in i.message for i in issues)


def test_beat_in_two_scenes(bible, characters, world, manifest, config):
    world.scenes[1].beat_ids.append("beat_001")
    issues = checks.check_world(bible, characters, world, manifest, config)
    assert any(i.category == "beat_coverage" for i in issues)


def test_beat_missing_from_all_scenes(bible, characters, world, manifest, config):
    world.scenes[1].beat_ids = ["beat_001"]
    world.scenes[0].beat_ids = ["beat_001"]
    issues = checks.check_world(bible, characters, world, manifest, config)
    assert any("beat_002 is not in any scene" in i.message for i in issues)


def test_scene_duration_out_of_tolerance(bible, characters, world, manifest, config):
    world.scenes[0].estimated_duration_sec = 60
    issues = checks.check_world(bible, characters, world, manifest, config)
    assert any(i.category == "duration" and i.severity == "error" for i in issues)


def test_wrong_outfit_owner(bible, characters, world, manifest, config):
    world.scenes[0].cast[1].outfit_id = "out_mia_default"
    issues = checks.check_world(bible, characters, world, manifest, config)
    assert any(i.category == "outfit" for i in issues)


def test_verbatim_line_must_appear(bible, characters, world, direction, config):
    direction.scenes[0].shots[0].dialogue.clear()
    issues = checks.check_direction(bible, characters, world, direction, config)
    assert any(i.category == "dialogue" for i in issues)
    config.flags.strict_dialogue = False
    issues = checks.check_direction(bible, characters, world, direction, config)
    assert not any(i.category == "dialogue" for i in issues)


def test_unknown_anchor_in_shot(bible, characters, world, direction, config):
    direction.scenes[0].shots[0].characters[0].to_anchor_id = "anc_moon"
    issues = checks.check_direction(bible, characters, world, direction, config)
    assert any("anc_moon" in i.message for i in issues)


def test_layout_clip_rig_mismatch(characters, world, direction, layout, manifest, config):
    layout.shots[0].characters[1].clips[0].clip_id = "clip_walk_cautious"  # human clip on the fox
    issues = checks.check_layout(characters, world, direction, layout, manifest, config)
    assert any(i.category == "clip_mismatch" for i in issues)


def test_layout_frames_must_be_contiguous(characters, world, direction, layout, manifest, config):
    layout.shots[1].frame_start = 250
    issues = checks.check_layout(characters, world, direction, layout, manifest, config)
    assert any(i.category == "timeline" for i in issues)


def test_layout_character_out_of_frame_is_warning(characters, world, direction, layout, manifest, config):
    cam = layout.shots[0].camera.keyframes[0]
    cam.look_at_m = (50, 50, 0)  # look away from everyone
    issues = checks.check_layout(characters, world, direction, layout, manifest, config)
    warned = [i for i in issues if i.category == "out_of_frame"]
    assert warned and all(i.severity == "warning" for i in warned)


def test_layout_path_outside_bounds(characters, world, direction, layout, manifest, config):
    layout.shots[0].characters[0].path[1].position_m = (500, 0, 0)
    issues = checks.check_layout(characters, world, direction, layout, manifest, config)
    assert any(i.category == "out_of_frame" and i.severity == "error" for i in issues)


def test_layout_missing_dialogue_timing(characters, world, direction, layout, manifest, config):
    layout.shots[0].dialogue_timing.clear()
    issues = checks.check_layout(characters, world, direction, layout, manifest, config)
    assert any(i.category == "dialogue" for i in issues)
