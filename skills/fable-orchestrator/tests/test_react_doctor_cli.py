from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import FAKE_DOCTOR_VERSION, doctor, fake_doctor_binary

OK_REPORT = {
    "ok": True,
    "score": 91,
    "diagnostics": [{"plugin": "react-doctor", "rule": "no-array-index-key", "file": "a.tsx"}],
}
FAILED_REPORT = {"ok": False, "score": 40, "diagnostics": []}
DEFAULT_UMASK = 0o022


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    """Invoke the wrapper CLI in-process and return (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = doctor.main(argv)
        except SystemExit as error:  # pragma: no cover - defensive
            code = error.code
    return code, out.getvalue(), err.getvalue()


def snapshot_file(directory: Path, name: str, report: dict[str, object]) -> Path:
    path = directory / name
    path.write_text(json.dumps(doctor.normalize(report, FAKE_DOCTOR_VERSION, "test")))
    return path


class NetworkScriptTests(unittest.TestCase):
    def test_npm_x_and_bun_x_are_treated_as_network(self):
        for command in ("npm x react-doctor", "bun x react-doctor", "npm  exec react-doctor"):
            with self.subTest(command=command):
                self.assertIsNotNone(doctor.NETWORK_SCRIPT.search(command))

    def test_known_network_runners_are_treated_as_network(self):
        for command in ("npx react-doctor", "pnpx react-doctor", "bunx rd", "pnpm dlx rd", "rd@latest"):
            with self.subTest(command=command):
                self.assertIsNotNone(doctor.NETWORK_SCRIPT.search(command))

    def test_local_exec_and_plain_binary_scripts_are_allowed(self):
        for command in ("pnpm exec react-doctor", "react-doctor scan", "yarn react-doctor"):
            with self.subTest(command=command):
                self.assertIsNone(doctor.NETWORK_SCRIPT.search(command))


class WriteJsonTests(unittest.TestCase):
    def test_write_json_creates_0600_even_without_chmod(self):
        previous = os.umask(DEFAULT_UMASK)
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "nested" / "snapshot.json"
                with patch.object(doctor.Path, "chmod", lambda self, mode: None):
                    doctor.write_json(path, {"score": 80})
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertEqual(json.loads(path.read_text())["score"], 80)
        finally:
            os.umask(previous)

    def test_write_json_fixes_preexisting_0644(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            path.write_text("{}")
            path.chmod(0o644)
            doctor.write_json(path, {"score": 10})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_write_json_refuses_symlink_target(self):
        if not getattr(os, "O_NOFOLLOW", 0):
            self.skipTest("platform has no O_NOFOLLOW")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target.json"
            target.write_text("original")
            path = Path(directory) / "snapshot.json"
            os.symlink(target, path)
            with self.assertRaises(OSError):
                doctor.write_json(path, {"score": 10})
            self.assertEqual(target.read_text(), "original")


class LoadSnapshotTests(unittest.TestCase):
    def test_compare_malformed_json_exits_2(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = Path(directory) / "baseline.json"
            baseline.write_text("{")
            final = snapshot_file(Path(directory), "final.json", OK_REPORT)
            code, _, err = run_cli(["compare", str(baseline), str(final)])
            self.assertEqual(code, 2)
            self.assertIn("react-doctor wrapper error:", err)

    def test_compare_non_object_exits_2(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = snapshot_file(Path(directory), "baseline.json", OK_REPORT)
            final = Path(directory) / "final.json"
            final.write_text("[1, 2]")
            code, _, err = run_cli(["compare", str(baseline), str(final)])
            self.assertEqual(code, 2)
            self.assertIn("snapshot is not a JSON object: final.json", err)

    def test_compare_missing_file_exits_2(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = snapshot_file(Path(directory), "baseline.json", OK_REPORT)
            code, _, err = run_cli(["compare", str(baseline), str(Path(directory) / "absent.json")])
            self.assertEqual(code, 2)
            self.assertIn("react-doctor wrapper error:", err)


class ScanTests(unittest.TestCase):
    def scan(self, argv: list[str], report: object, project: Path) -> tuple[int, str, str]:
        fake_doctor_binary(project)
        payload = report if isinstance(report, str) else json.dumps(report)
        with patch.dict(os.environ, {"FAKE_DOCTOR_JSON": payload}):
            return run_cli(argv)

    def test_scan_with_fake_binary_writes_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            output = project / "out" / "snapshot.json"
            code, out, _ = self.scan(
                ["scan", "--project", str(project), "--output", str(output)], OK_REPORT, project
            )
            self.assertEqual(code, 0)
            snapshot = json.loads(output.read_text())
            self.assertEqual(snapshot["version"], FAKE_DOCTOR_VERSION)
            self.assertEqual(snapshot["source"], "project-binary")
            self.assertEqual((snapshot["score"], snapshot["finding_count"]), (91, 1))
            self.assertEqual(json.loads(out)["finding_ids"], snapshot["finding_ids"])
            self.assertIn("--scope full", (project / "doctor-args.txt").read_text())

    def test_scan_with_base_uses_changed_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            output = project / "snapshot.json"
            code, _, _ = self.scan(
                [
                    "scan",
                    "--project",
                    str(project),
                    "--base",
                    "HEAD~1",
                    "--output",
                    str(output),
                    "--max-duration",
                    "5",
                ],
                OK_REPORT,
                project,
            )
            self.assertEqual(code, 0)
            recorded = (project / "doctor-args.txt").read_text()
            self.assertIn("--scope changed --base HEAD~1 --include-untracked", recorded)
            self.assertIn("--max-duration 5", recorded)

    def test_scan_returns_2_when_report_not_ok(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            output = project / "snapshot.json"
            code, _, _ = self.scan(
                ["scan", "--project", str(project), "--output", str(output)], FAILED_REPORT, project
            )
            self.assertEqual(code, 2)
            self.assertFalse(json.loads(output.read_text())["ok"])

    def test_scan_without_json_output_is_wrapper_error(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            output = project / "snapshot.json"
            code, _, err = self.scan(
                ["scan", "--project", str(project), "--output", str(output)], "", project
            )
            self.assertEqual(code, 2)
            self.assertIn("react-doctor wrapper error:", err)
            self.assertIn("did not emit a JSON object", err)
            self.assertFalse(output.exists())


class DispatchTests(unittest.TestCase):
    def test_discover_cli_prints_source(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            fake_doctor_binary(project)
            code, out, _ = run_cli(["discover", "--project", str(project)])
            self.assertEqual(code, 0)
            payload = json.loads(out)
            self.assertEqual(payload["source"], "project-binary")
            resolved = project.resolve() / "node_modules" / ".bin" / "react-doctor"
            self.assertEqual(payload["argv"], [str(resolved)])

    def test_discover_cli_reports_missing_react_doctor(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(doctor.shutil, "which", return_value=None):
                code, _, err = run_cli(["discover", "--project", directory])
            self.assertEqual(code, 2)
            self.assertIn("React Doctor is unavailable", err)

    def test_compare_cli_happy_path(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = snapshot_file(Path(directory), "baseline.json", OK_REPORT)
            final = snapshot_file(Path(directory), "final.json", FAILED_REPORT | {"ok": True})
            code, out, _ = run_cli(["compare", str(baseline), str(final)])
            self.assertEqual(code, 0)
            payload = json.loads(out)
            self.assertTrue(payload["comparable"])
            self.assertEqual(payload["score_delta"], -51)

    def test_self_test_cli_passes(self):
        code, out, _ = run_cli(["self-test"])
        self.assertEqual(code, 0)
        self.assertIn("self-test passed", out)


class ReportParsingTests(unittest.TestCase):
    def test_parse_json_output_reads_the_last_json_line(self):
        output = "noise\n{\"broken\": \n{\"ok\": true}\n"
        self.assertEqual(doctor.parse_json_output(output), {"ok": True})

    def test_parse_json_output_rejects_empty_and_non_object_output(self):
        for output in ("", "just noise\n", "[1, 2]\n"):
            with self.subTest(output=output):
                with self.assertRaisesRegex(ValueError, "did not emit a JSON object"):
                    doctor.parse_json_output(output)

    def test_score_value_handles_floats_and_nested_objects(self):
        self.assertEqual(doctor.score_value(82.4), 82)
        self.assertEqual(doctor.score_value({"value": 70}), 70)
        self.assertIsNone(doctor.score_value(True))
        self.assertIsNone(doctor.score_value(150))
        self.assertIsNone(doctor.score_value({"score": "high"}))

    def test_report_score_prefers_summary_then_minimum_project_score(self):
        self.assertEqual(doctor.report_score({"summary": {"score": 77.6}}), 78)
        self.assertEqual(
            doctor.report_score({"projects": [{"score": 90}, {"score": 55}, "junk"]}), 55
        )
        self.assertIsNone(doctor.report_score({}))
        self.assertIsNone(doctor.report_score({"projects": [{"score": None}]}))

    def test_diagnostics_falls_back_to_project_entries(self):
        report = {"projects": ["junk", {"diagnostics": "no"}, {"diagnostics": [{"rule": "a"}, 5]}]}
        self.assertEqual(doctor.diagnostics(report), [{"rule": "a"}])

    def test_compare_is_not_comparable_when_failed_or_unversioned(self):
        baseline = doctor.normalize(OK_REPORT, "unknown", "test")
        final = doctor.normalize(OK_REPORT, "unknown", "test")
        self.assertFalse(doctor.compare(baseline, final)["comparable"])
        self.assertEqual(doctor.compare(baseline, final)["version"], "version-mismatch")
        failed = doctor.normalize(FAILED_REPORT, FAKE_DOCTOR_VERSION, "test")
        result = doctor.compare(doctor.normalize(OK_REPORT, FAKE_DOCTOR_VERSION, "test"), failed)
        self.assertFalse(result["comparable"])
        self.assertEqual(result["new_finding_ids"], [])


class CommandDiscoveryTests(unittest.TestCase):
    def test_package_manager_reads_field_then_lockfiles(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            self.assertEqual(doctor.package_manager(project, {"packageManager": "yarn@4.1.0"}), "yarn")
            self.assertEqual(doctor.package_manager(project, {}), "npm")
            (project / "bun.lockb").write_text("")
            self.assertEqual(doctor.package_manager(project, {"packageManager": 7}), "bun")
            (project / "yarn.lock").write_text("")
            self.assertEqual(doctor.package_manager(project, {}), "yarn")

    def test_script_command_matches_each_manager(self):
        self.assertEqual(doctor.script_command("npm", "d"), ("npm", "run", "--silent", "d", "--"))
        self.assertEqual(doctor.script_command("pnpm", "d"), ("pnpm", "--silent", "run", "d", "--"))
        self.assertEqual(doctor.script_command("yarn", "d"), ("yarn", "--silent", "run", "d"))
        self.assertEqual(doctor.script_command("bun", "d"), ("bun", "run", "--silent", "d", "--"))

    def test_discover_prefers_a_local_package_script(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "package.json").write_text(
                json.dumps({"scripts": {"lint": "react-doctor .", "doctor": "react-doctor ."}})
            )
            with patch.object(doctor.shutil, "which", return_value="/fake/bin/npm"):
                command = doctor.discover(project)
            self.assertEqual(command.source, "package-script:doctor")
            self.assertEqual(command.argv, ("npm", "run", "--silent", "doctor", "--"))

    def test_discover_ignores_scripts_when_the_manager_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "package.json").write_text(json.dumps({"scripts": {"doctor": "react-doctor"}}))
            with patch.object(doctor.shutil, "which", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "React Doctor is unavailable"):
                    doctor.discover(project)

    def test_discover_uses_the_pinned_npx_fallback_only_when_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            with patch.object(
                doctor.shutil, "which", side_effect=lambda name: "/fake/bin/npx" if name == "npx" else None
            ):
                command = doctor.discover(project, allow_pinned_npx=True)
            self.assertEqual(command.source, "approved-pinned-npx")
            self.assertEqual(command.argv[-1], doctor.PINNED_NPX_SPEC)

    def test_discover_uses_an_installed_command(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                doctor.shutil,
                "which",
                side_effect=lambda name: "/fake/bin/react-doctor" if name == "react-doctor" else None,
            ):
                command = doctor.discover(Path(directory))
            self.assertEqual(command.source, "installed-command")

    def test_command_version_reads_and_falls_back_to_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            binary = fake_doctor_binary(project)
            known = doctor.DoctorCommand("project-binary", (str(binary),))
            self.assertEqual(doctor.command_version(known, project), FAKE_DOCTOR_VERSION)
            silent = doctor.DoctorCommand("test", ("/bin/echo", "no version here"))
            self.assertEqual(doctor.command_version(silent, project), "unknown")


if __name__ == "__main__":
    unittest.main()
