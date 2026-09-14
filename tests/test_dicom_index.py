# SPDX-License-Identifier: Apache-2.0
"""Grouping a DICOM pile into patients, studies and 4D time points.

This is the highest-consequence guess in the pipeline: a wrong answer produces
volumes that open cleanly, have sane geometry, and describe motion that never
happened. Each rung of the tag ladder is exercised on its own, and the case
where two rungs disagree is checked to report rather than resolve.
"""

import numpy as np
import pytest

from openh4d import dicom_index
from openh4d.dicom_index import (
    CONFIDENCE_LOW,
    build_plan,
    order_slices,
    phase_from_description,
    recover_timepoints,
    scan,
)
from openh4d.synthetic_dicom import write_multi_patient_source, write_series

SMALL = (8, 8, 4)


@pytest.fixture(scope="module")
def sources(tmp_path_factory):
    """One source per temporal encoding, generated once."""
    root = tmp_path_factory.mktemp("dicom-sources")
    out = {}
    for encoding in dicom_index.__dict__ and (
        "temporal_position",
        "series_description",
        "trigger_time",
        "acquisition_number",
        "none",
    ):
        out[encoding] = write_series(
            root / encoding, encoding=encoding, n_timepoints=4, shape=SMALL
        )
    return out


# --- phase tokens ------------------------------------------------------------


@pytest.mark.parametrize(
    "description,fraction,label",
    [
        ("4D CT Phase 0.0%", 0.0, "0.0%"),
        ("4D CT Phase 50.0%", 0.5, "50.0%"),
        ("Resp 90%", 0.9, "90%"),
        ("Phase 100%", 1.0, "100%"),
        ("exhale", 0.0, "exhale"),
        ("Inhale breath-hold", 0.5, "Inhale"),
    ],
)
def test_phase_from_description(description, fraction, label):
    got_fraction, got_label = phase_from_description(description)
    assert got_fraction == pytest.approx(fraction)
    assert got_label == label


@pytest.mark.parametrize("description", [None, "", "Chest CT", "Series 3", "120%"])
def test_phase_from_description_finds_nothing(description):
    assert phase_from_description(description) == (None, None)


# --- the ladder --------------------------------------------------------------


@pytest.mark.parametrize(
    "encoding,expected_source",
    [
        ("temporal_position", "TemporalPositionIdentifier"),
        ("series_description", "SeriesDescription"),
        ("trigger_time", "TriggerTime"),
        ("acquisition_number", "AcquisitionNumber"),
    ],
)
def test_each_rung_fires_on_its_own_encoding(sources, encoding, expected_source):
    files, _ = scan(sources[encoding])
    chosen, _ = recover_timepoints(files)
    assert chosen is not None
    assert chosen.source == expected_source
    assert chosen.n_timepoints == 4


def test_ladder_priority_prefers_the_cleanest_signal(tmp_path):
    """TemporalPositionIdentifier outranks the weaker rungs when both are present."""
    source = write_series(
        tmp_path / "both", encoding="temporal_position", n_timepoints=4, shape=SMALL
    )
    files, _ = scan(source)
    chosen, fired = recover_timepoints(files)
    assert chosen.source == "TemporalPositionIdentifier"
    assert len(fired) > 1, "expected a weaker rung to also fire, so priority is being tested"


def test_no_temporal_tag_yields_a_clear_failure(sources):
    """Falling back to InstanceNumber must be flagged, never applied silently."""
    files, _ = scan(sources["none"])
    chosen, _ = recover_timepoints(files)
    assert chosen is None or chosen.source == "InstanceNumber"
    if chosen is not None:
        assert chosen.confidence == CONFIDENCE_LOW


def test_instance_number_rung_requires_confirmation(sources):
    plan = build_plan(sources["none"])
    study = plan["patients"][0]["studies"][0]
    assert study["requires_confirmation"] is True


# --- slice ordering ----------------------------------------------------------


def test_slices_are_ordered_by_geometry_not_instance_number(sources):
    """SliceLocation has an unreliable sign and InstanceNumber reflects transfer order."""
    files, _ = scan(sources["series_description"])
    chosen, _ = recover_timepoints(files)
    group = chosen.groups[chosen.ordered_keys()[0]]

    shuffled = list(reversed(group))
    ordered = order_slices(shuffled)
    offsets = [f.slice_offset() for f in ordered]
    assert offsets == sorted(offsets)


def test_slice_offset_uses_the_slice_normal(sources):
    files, _ = scan(sources["series_description"])
    first = files[0]
    assert first.image_orientation == (1, 0, 0, 0, 1, 0)
    # With that orientation the normal is +z, so the offset is the z position.
    assert first.slice_offset() == pytest.approx(first.image_position[2])


# --- study assembly ----------------------------------------------------------


def test_plan_shape_for_a_single_study(sources):
    plan = build_plan(sources["series_description"])
    assert plan["n_dicom_files"] == 16
    assert len(plan["patients"]) == 1

    study = plan["patients"][0]["studies"][0]
    assert study["n_timepoints"] == 4
    assert study["phase_source"] == "SeriesDescription"
    assert study["modality"] == "CT"
    assert [tp["index"] for tp in study["timepoints"]] == [0, 1, 2, 3]
    assert [tp["n_files"] for tp in study["timepoints"]] == [4, 4, 4, 4]


def test_plan_records_the_verbatim_phase_labels(sources):
    study = build_plan(sources["series_description"])["patients"][0]["studies"][0]
    assert [tp["label"] for tp in study["timepoints"]] == ["0.0%", "25.0%", "50.0%", "75.0%"]
    assert [tp["phase_fraction"] for tp in study["timepoints"]] == [0.0, 0.25, 0.5, 0.75]


def test_plan_recovers_geometry(sources):
    study = build_plan(sources["series_description"])["patients"][0]["studies"][0]
    assert study["geometry"]["in_plane_mm"] == [0.8, 0.8]
    assert study["geometry"]["slice_spacing_mm"] == pytest.approx(1.0)
    assert study["geometry"]["matrix"] == [8, 8, 4]


def test_multiple_patients_and_studies(tmp_path):
    source = write_multi_patient_source(
        tmp_path / "multi", n_patients=2, n_timepoints=4, shape=SMALL
    )
    plan = build_plan(source)
    assert len(plan["patients"]) == 2
    counts = {p["source_patient_id"]: p["n_studies"] for p in plan["patients"]}
    assert counts == {"SRC-PATIENT-001": 2, "SRC-PATIENT-002": 1}


def test_study_order_is_deterministic(tmp_path):
    """A published study letter is part of a citable identifier, so order must be stable."""
    source = write_multi_patient_source(tmp_path / "m", n_patients=2, n_timepoints=2, shape=SMALL)
    first = build_plan(source)
    second = build_plan(source)
    uids = lambda plan: [  # noqa: E731
        (p["source_patient_id"], s["study_instance_uid"])
        for p in plan["patients"]
        for s in p["studies"]
    ]
    assert uids(first) == uids(second)


def test_non_dicom_files_are_skipped_not_fatal(tmp_path):
    """A contributor export routinely contains DICOMDIR, thumbnails and stray notes."""
    source = write_series(tmp_path / "s", n_timepoints=2, shape=SMALL)
    (source / "NOTES.txt").write_text("scratch", encoding="utf-8")
    (source / "DICOMDIR").write_bytes(b"not a real dicomdir")

    plan = build_plan(source)
    assert plan["n_skipped_files"] == 2
    assert plan["patients"][0]["studies"][0]["n_timepoints"] == 2


def test_missing_patient_id_falls_back_to_a_stable_hash(tmp_path):
    source = write_series(tmp_path / "s", patient_id="", n_timepoints=2, shape=SMALL)
    plan = build_plan(source)
    key = plan["patients"][0]["source_patient_id"]
    assert key.startswith("UNKNOWN-")
    assert build_plan(source)["patients"][0]["source_patient_id"] == key


# --- disagreement is reported, not resolved ----------------------------------


def test_disagreeing_heuristics_are_reported(monkeypatch, sources):
    """Silently picking one answer is the worst failure available here."""
    files, _ = scan(sources["series_description"])

    real = dicom_index._by_acquisition_number

    def disagreeing(files):
        grouping = dicom_index.Grouping(
            source="AcquisitionNumber",
            groups={0: files[:8], 1: files[8:]},
            labels={0: "a", 1: "b"},
        )
        return grouping

    monkeypatch.setattr(dicom_index, "_by_acquisition_number", disagreeing)
    monkeypatch.setattr(
        dicom_index,
        "LADDER",
        (dicom_index._by_series_description_phase, disagreeing),
    )

    chosen, fired = recover_timepoints(files)
    assert chosen.n_timepoints == 4
    assert {g.n_timepoints for g in fired} == {4, 2}
    assert real is not None  # the real rung is untouched outside this test


def test_disagreement_lowers_confidence_and_demands_review(monkeypatch, sources):
    def disagreeing(files):
        return dicom_index.Grouping(
            source="AcquisitionNumber",
            groups={0: files[:8], 1: files[8:]},
            labels={0: "a", 1: "b"},
        )

    monkeypatch.setattr(
        dicom_index,
        "LADDER",
        (dicom_index._by_series_description_phase, disagreeing),
    )
    study = build_plan(sources["series_description"])["patients"][0]["studies"][0]
    assert study["confidence"] == CONFIDENCE_LOW
    assert study["requires_confirmation"] is True
    assert any("disagree" in problem for problem in study["problems"])
    assert study["alternatives"] == [{"source": "AcquisitionNumber", "n_timepoints": 2}]


def test_differing_slice_counts_across_time_points_is_a_problem(monkeypatch, sources):
    files, _ = scan(sources["series_description"])

    def uneven(_files):
        return dicom_index.Grouping(
            source="SeriesDescription",
            groups={0.0: files[:4], 0.5: files[4:7]},
            labels={0.0: "0%", 0.5: "50%"},
        )

    monkeypatch.setattr(dicom_index, "LADDER", (uneven,))
    study = build_plan(sources["series_description"])["patients"][0]["studies"][0]
    assert any("slice counts" in problem for problem in study["problems"])
    assert study["confidence"] == CONFIDENCE_LOW


def test_duplicate_slice_positions_are_flagged(sources):
    """Two phases merged into one time point shows up as repeated slice positions."""
    files, _ = scan(sources["series_description"])
    chosen, _ = recover_timepoints(files)
    group = chosen.groups[chosen.ordered_keys()[0]]
    spacing, problems = dicom_index._slice_spacing(group + group)
    assert any("more than once" in problem for problem in problems)


def test_non_uniform_slice_spacing_is_flagged(sources):
    files, _ = scan(sources["series_description"])
    chosen, _ = recover_timepoints(files)
    group = list(chosen.groups[chosen.ordered_keys()[0]])
    group[-1].image_position = (0.0, 0.0, 99.0)
    _, problems = dicom_index._slice_spacing(group)
    assert any("not uniform" in problem for problem in problems)


# --- summary -----------------------------------------------------------------


def test_summary_names_the_heuristic_that_fired(sources):
    summary = dicom_index.summarize(build_plan(sources["series_description"]))
    assert "SeriesDescription" in summary
    assert "4 time point(s)" in summary


def test_cli_returns_two_when_review_is_needed(sources, capsys):
    """Exit 2 means 'indexed, but a human must confirm before applying'."""
    assert dicom_index.main([str(sources["none"]), "--summary"]) == 2
    assert dicom_index.main([str(sources["series_description"]), "--summary"]) == 0
    capsys.readouterr()


def test_pixel_data_round_trips_through_the_fixture(sources):
    """The fixture must carry the real moving sphere, or every motion check is vacuous."""
    import pydicom

    files, _ = scan(sources["series_description"])
    chosen, _ = recover_timepoints(files)
    first = order_slices(chosen.groups[chosen.ordered_keys()[0]])[2]
    mid = order_slices(chosen.groups[chosen.ordered_keys()[2]])[2]

    a = pydicom.dcmread(str(first.path)).pixel_array
    b = pydicom.dcmread(str(mid.path)).pixel_array
    assert a.shape == b.shape
    assert not np.array_equal(a, b), "time points must differ, or there is no motion to measure"
