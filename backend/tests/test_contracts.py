import uuid

import pytest
from pydantic import BaseModel, ValidationError

from studio_contracts.ids import AssetKind, VersionNumber, display_code, parse_display_code, uuid7
from studio_contracts.jobs import JobEnvelope, JobType


def test_uuid7_version_variant_and_order():
    ids = [uuid7() for _ in range(200)]
    assert all(i.version == 7 and i.variant == uuid.RFC_4122 for i in ids)
    # the 48-bit millisecond prefix never decreases
    prefixes = [i.int >> 80 for i in ids]
    assert prefixes == sorted(prefixes)
    assert len(set(ids)) == len(ids)


def test_display_codes_round_trip():
    assert display_code(AssetKind.character, 1) == "CHAR-001"
    assert display_code(AssetKind.outfit, 1234) == "OUTFIT-1234"
    assert parse_display_code("OUTFIT-002") == (AssetKind.outfit, 2)
    for bad in ("CHAR-1", "char-001", "NOPE-001"):
        with pytest.raises(ValueError):
            parse_display_code(bad)
    with pytest.raises(ValueError):
        display_code(AssetKind.character, 0)


def test_version_numbers_compare_numerically_and_bump():
    assert VersionNumber.parse("1.10") > VersionNumber.parse("1.9")
    assert str(VersionNumber.parse("2.0").bump("minor")) == "2.1"
    assert str(VersionNumber.parse("2.3").bump("major")) == "3.0"
    for bad in ("1", "1.0.0", "01.0", "0.0", "a.b"):
        with pytest.raises(ValueError):
            VersionNumber.parse(bad)


def test_version_number_in_pydantic():
    class M(BaseModel):
        v: VersionNumber

    assert M(v="1.2").v == VersionNumber(1, 2)
    assert M(v="1.2").model_dump(mode="json") == {"v": "1.2"}
    with pytest.raises(ValidationError):
        M(v="1")


def test_job_envelope_is_strict():
    env = JobEnvelope(job_id=uuid7(), type=JobType.blender_smoke, attempt=1, params={}, contracts_version="0.1.0")
    assert JobEnvelope.model_validate_json(env.model_dump_json()) == env
    with pytest.raises(ValidationError):
        JobEnvelope.model_validate({**env.model_dump(), "surprise": 1})
