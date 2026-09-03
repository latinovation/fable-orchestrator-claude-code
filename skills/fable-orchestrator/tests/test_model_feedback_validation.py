from __future__ import annotations

import unittest

from support import feedback

VALID_TIMESTAMP = "2026-09-03T12:00:00+00:00"
MICROSECOND_TIMESTAMP = "2026-09-03T12:00:00.123456+00:00"


def raw_record(**overrides) -> dict[str, object]:
    base: dict[str, object] = {
        "run_id": "validation-run-1",
        "stage": "complete",
        "task_class": "routine",
        "planned_model": "sonnet",
        "actual_model": "sonnet",
        "planner_actual_model": "fable",
        "auditor_actual_model": "fable",
        "outcome": "pass",
        "model_fit": "right-sized",
        "planner_quality": "strong",
        "executor_quality": "strong",
        "auditor_quality": "no-findings",
    }
    return {**base, **overrides}


class RecordedAtTests(unittest.TestCase):
    def test_recorded_at_rejects_free_text(self):
        with self.assertRaisesRegex(ValueError, "recorded_at must be an ISO-8601 UTC timestamp"):
            feedback.validate(raw_record(recorded_at="yesterday, around noon"))

    def test_recorded_at_rejects_non_string(self):
        with self.assertRaisesRegex(ValueError, "recorded_at must be an ISO-8601 UTC timestamp"):
            feedback.validate(raw_record(recorded_at=1234567890))

    def test_recorded_at_rejects_non_ascii_digits(self):
        with self.assertRaisesRegex(ValueError, "recorded_at must be an ISO-8601 UTC timestamp"):
            feedback.validate(raw_record(recorded_at="\u0662\u0660\u0662\u0666-09-03T00:00:00+00:00"))

    def test_recorded_at_keeps_valid_timestamp_with_and_without_microseconds(self):
        for timestamp in (VALID_TIMESTAMP, MICROSECOND_TIMESTAMP):
            with self.subTest(timestamp=timestamp):
                record = feedback.validate(raw_record(recorded_at=timestamp))
                self.assertEqual(record["recorded_at"], timestamp)

    def test_recorded_at_defaults_to_now_when_absent_or_null(self):
        for value in ({}, {"recorded_at": None}):
            with self.subTest(value=value):
                record = feedback.validate(raw_record(**value))
                self.assertRegex(str(record["recorded_at"]), feedback.RECORDED_AT.pattern)

    def test_now_iso_matches_the_accepted_pattern(self):
        self.assertRegex(feedback.now_iso(), feedback.RECORDED_AT.pattern)


class TagTests(unittest.TestCase):
    def test_tags_reject_non_ascii(self):
        for tag in ("ñandú", "CAFÉ"):
            with self.subTest(tag=tag):
                with self.assertRaisesRegex(ValueError, "invalid tag"):
                    feedback.validate(raw_record(tags=[tag]))

    def test_tags_accept_hyphenated_ascii_and_lowercase(self):
        record = feedback.validate(raw_record(tags=["Multi-File", "ui"]))
        self.assertEqual(record["tags"], ["multi-file", "ui"])

    def test_tags_reject_leading_hyphen_and_overlong_labels(self):
        for tag in ("-leading", "x" * 33, "", "with space"):
            with self.subTest(tag=tag):
                with self.assertRaisesRegex(ValueError, "invalid tag"):
                    feedback.validate(raw_record(tags=[tag]))

    def test_tags_reject_non_list_and_overlong_list(self):
        with self.assertRaisesRegex(ValueError, "tags must be a list"):
            feedback.validate(raw_record(tags="ui"))
        with self.assertRaisesRegex(ValueError, "tags must be a list"):
            feedback.validate(raw_record(tags=[f"tag-{index}" for index in range(9)]))


class EnumAndCountTests(unittest.TestCase):
    def test_missing_outcome_has_clear_message(self):
        raw = raw_record()
        del raw["outcome"]
        with self.assertRaisesRegex(ValueError, "^missing outcome$"):
            feedback.validate(raw)

    def test_invalid_outcome_reports_the_offending_value(self):
        with self.assertRaisesRegex(ValueError, "invalid outcome: 'NO-EXISTE'"):
            feedback.validate(raw_record(outcome="NO-EXISTE"))

    def test_enum_rejects_non_string_values(self):
        for value in (["pass"], {}):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "invalid outcome"):
                    feedback.validate(raw_record(outcome=value))

    def test_counts_reject_non_integer(self):
        with self.assertRaisesRegex(ValueError, "repair_cycles"):
            feedback.validate(raw_record(repair_cycles="broken"))

    def test_counts_reject_boolean_and_negative(self):
        for value in (True, -1):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "checks_failed"):
                    feedback.validate(raw_record(checks_failed=value))


class StructureTests(unittest.TestCase):
    def test_non_object_and_unsafe_run_id_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "record must be a JSON object"):
            feedback.validate([1, 2])
        with self.assertRaisesRegex(ValueError, "run_id must be"):
            feedback.validate(raw_record(run_id="short"))

    def test_invalid_stage_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid stage"):
            feedback.validate(raw_record(stage="finished"))

    def test_react_doctor_fields_are_validated(self):
        with self.assertRaisesRegex(ValueError, "react_doctor_applicable must be boolean"):
            feedback.validate(raw_record(react_doctor_applicable="yes"))
        with self.assertRaisesRegex(ValueError, "react_doctor_baseline_score"):
            feedback.validate(raw_record(react_doctor_baseline_score=101))
        with self.assertRaisesRegex(ValueError, "react_doctor_new_findings"):
            feedback.validate(raw_record(react_doctor_new_findings=-2))
        with self.assertRaisesRegex(ValueError, "react_doctor_version"):
            feedback.validate(raw_record(react_doctor_version="not a version"))

    def test_executor_agent_is_derived_from_planned_model(self):
        self.assertEqual(feedback.validate(raw_record())["executor_agent"], "sonnet-executor")
        self.assertEqual(
            feedback.validate(raw_record(planned_model="unknown"))["executor_agent"], "not-run"
        )


if __name__ == "__main__":
    unittest.main()
