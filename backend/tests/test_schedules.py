from __future__ import annotations

import plistlib
import tempfile
import unittest
from pathlib import Path

from app import schedules


def _write(dirpath: Path, label: str, plist: dict) -> None:
    plist.setdefault("Label", label)
    (dirpath / f"{label}.plist").write_bytes(plistlib.dumps(plist))


class ScheduleSummaryTests(unittest.TestCase):
    def test_weekdays(self):
        p = {"StartCalendarInterval": [{"Weekday": d, "Hour": 20, "Minute": 0} for d in range(1, 6)]}
        self.assertEqual(schedules.summarize_schedule(p), "Weekdays at 20:00")

    def test_specific_days(self):
        p = {"StartCalendarInterval": [
            {"Weekday": 3, "Hour": 16, "Minute": 30},
            {"Weekday": 5, "Hour": 16, "Minute": 30},
        ]}
        self.assertEqual(schedules.summarize_schedule(p), "Wed, Fri at 16:30")

    def test_interval(self):
        self.assertEqual(schedules.summarize_schedule({"StartInterval": 3600}), "every 1h")

    def test_sunday_normalization(self):
        # launchd uses 0 or 7 for Sunday
        p = {"StartCalendarInterval": [{"Weekday": 7, "Hour": 9, "Minute": 0}]}
        self.assertEqual(schedules.summarize_schedule(p), "Sun at 09:00")


class ScheduleBuildTests(unittest.TestCase):
    def test_build_calendar_interval(self):
        out = schedules.build_calendar_interval(16, 30, [3, 5])
        self.assertEqual(out, [
            {"Weekday": 3, "Hour": 16, "Minute": 30},
            {"Weekday": 5, "Hour": 16, "Minute": 30},
        ])

    def test_build_clamps_and_defaults(self):
        out = schedules.build_calendar_interval(99, -5, [])
        self.assertEqual(out[0]["Hour"], 23)
        self.assertEqual(out[0]["Minute"], 0)
        self.assertEqual([d["Weekday"] for d in out], [1, 2, 3, 4, 5])


class ScheduleListTests(unittest.TestCase):
    def test_list_and_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write(d, "com.x.daily", {
                "ProgramArguments": ["/bin/bash", "/Users/you/projects/foo/run_daily.sh"],
                "WorkingDirectory": "/Users/you/projects/foo",
                "StartCalendarInterval": [{"Weekday": 1, "Hour": 20, "Minute": 0}],
            })
            _write(d, "com.x.paused", {
                "ProgramArguments": ["/usr/bin/python3", "-m", "pkg.cli", "run", "--config", "/a/config.json"],
                "Disabled": True,
                "StartCalendarInterval": [{"Weekday": 5, "Hour": 9, "Minute": 0}],
            })
            status = {"com.x.daily": {"pid": None, "last_exit": 0}}
            tasks = schedules.list_tasks(agents_dir=tmp, status=status)
            by_label = {t["label"]: t for t in tasks}
            self.assertEqual(by_label["com.x.daily"]["status"], "active")
            self.assertEqual(by_label["com.x.daily"]["project"], "foo")
            self.assertEqual(by_label["com.x.daily"]["run_status"], "success")
            self.assertIn("run_daily.sh", by_label["com.x.daily"]["description"])
            self.assertEqual(by_label["com.x.paused"]["status"], "paused")
            self.assertIn("pkg.cli run", by_label["com.x.paused"]["description"])

    def test_reschedule_writes_plist(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write(d, "com.x.daily", {
                "ProgramArguments": ["/bin/bash", "/run.sh"],
                "StartCalendarInterval": [{"Weekday": 1, "Hour": 20, "Minute": 0}],
            })
            schedules.LAUNCH_AGENTS = d  # point action lookup at temp (launchctl no-ops here)
            res = schedules.reschedule("com.x.daily", 9, 15, [5])
            self.assertEqual(res["schedule"], "Fri at 09:15")
            saved = plistlib.loads((d / "com.x.daily.plist").read_bytes())
            self.assertEqual(saved["StartCalendarInterval"], [{"Weekday": 5, "Hour": 9, "Minute": 15}])


if __name__ == "__main__":
    unittest.main()
