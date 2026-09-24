"""Unit tests for the pure helpers of btrbk_tui_pro.py.

They mirror the Rust tests in btrbk_tui_rust/src/main.rs: both versions must
agree on what a snapshot name means and on what a purge may delete.

Run with:  python3 -m unittest discover -s tests -p '*_test.py'
(the file is not called test_*.py because .gitignore excludes that pattern)

This repository is public: hosts, paths and UUIDs below are placeholders.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import btrbk_tui_pro as tui

CONF = """\
transaction_log            /var/log/btrbk.log
ssh_identity /etc/btrbk/ssh/id_ed25519
volume /mnt/btr_pool
  target ssh://10.0.0.1:2222/mnt/backup/host-btrfs
  subvolume @
ssh_user backup
"""


class SshTargetTest(unittest.TestCase):
    def test_first_target_with_the_options_before_it(self):
        target = tui.parse_ssh_target(CONF)
        self.assertEqual(target, tui.SshTarget(
            host="10.0.0.1", port="2222", path="/mnt/backup/host-btrfs",
            user=None,  # declared after the target: not in effect for it
            identity="/etc/btrbk/ssh/id_ed25519"))
        self.assertIsNone(tui.parse_ssh_target("volume /mnt/btr_pool\n  subvolume @\n"))
        self.assertIsNone(tui.parse_ssh_target("target /mnt/local/backup\n"))

    def test_ports_ipv6_and_target_types(self):
        target = tui.parse_ssh_target("ssh_port 2200\ntarget send-receive ssh://nas.example/backup\n")
        self.assertEqual((target.host, target.port), ("nas.example", "2200"))

        target = tui.parse_ssh_target("ssh_port default\ntarget ssh://[fd00::1]:2222/backup\n")
        self.assertEqual((target.host, target.port), ("fd00::1", "2222"))

        target = tui.parse_ssh_target("ssh_identity no\ntarget ssh://[fd00::1]/backup\n")
        self.assertEqual((target.port, target.identity), (None, None))


class BtrfsOutputTest(unittest.TestCase):
    def test_received_uuids_skip_unset_ones(self):
        output = (
            "ID 33457 gen 27329461 top level 257 parent_uuid 40306c4a-a7f6-b443-a247-da3f59bfc1ca "
            "received_uuid c9e62952-9e79-e041-acde-4dc9e3323826 uuid 931a0611-0769-404a-9a79-fddbcc070729 "
            "path backup/host-btrfs/@games.20260803T0000\n"
            "ID 257 gen 1 top level 5 parent_uuid - received_uuid - "
            "uuid 0ed5ab3d-732e-4544-8522-10abc449a27b path backup\n"
        )
        self.assertEqual(tui.parse_received_uuids(output), {"c9e62952-9e79-e041-acde-4dc9e3323826"})

    def test_subvolume_uuid_ignores_parent_and_received(self):
        # a sloppy match would return "Parent UUID" or "Received UUID"
        output = (
            "/mnt/btr_pool/btrbk_snapshots/@games.20260803T0000\n"
            "\tName: \t\t\t@games.20260803T0000\n"
            "\tParent UUID: \t\tf914faf4-aae8-484b-90d4-dae5b2d6088a\n"
            "\tUUID: \t\t\tc9e62952-9e79-e041-acde-4dc9e3323826\n"
            "\tReceived UUID: \t\t-\n"
        )
        self.assertEqual(tui.parse_subvolume_uuid(output), "c9e62952-9e79-e041-acde-4dc9e3323826")
        self.assertIsNone(tui.parse_subvolume_uuid("no uuid here\n"))


class SnapshotNamesTest(unittest.TestCase):
    def test_names_split_at_the_last_dot(self):
        self.assertEqual(tui.split_snapshot_name("@home.20260803T0000"), ("@home", "20260803T0000"))
        self.assertEqual(tui.split_snapshot_name("@.20260803T0000_1"), ("@", "20260803T0000_1"))
        self.assertEqual(tui.split_snapshot_name("@my.data.20260803"), ("@my.data", "20260803"))
        # btrbk's default naming: the subvolume name, with or without "@"
        self.assertEqual(tui.split_snapshot_name("home.20250901T0800"), ("home", "20250901T0800"))
        # a dot alone does not make a snapshot: what follows must be a timestamp
        for not_a_snapshot in ("home", "@home.", ".20250901T0800", "scripts.d",
                               "prune_snapshots_keep_parent.sh", "@home.BROKEN"):
            self.assertIsNone(tui.split_snapshot_name(not_a_snapshot))

    def test_config_belongs_to_the_user_behind_sudo(self):
        candidates = tui.config_candidates(Path("/home/user"), Path("/root"))
        self.assertEqual(candidates, [Path(p) for p in (
            "/home/user/.config/btrbk_tui/config.json",
            "/root/.config/btrbk_tui/config.json",
            "/home/user/.config/btrbk_restore/config.json",
            "/root/.config/btrbk_restore/config.json",
        )])
        self.assertTrue(tui.is_legacy_config(candidates[2]))
        self.assertFalse(tui.is_legacy_config(candidates[0]))

        # plain root login: no duplicates, and never a world-writable fallback
        self.assertEqual(len(tui.config_candidates(None, Path("/root"))), 2)
        self.assertEqual(tui.config_candidates(None, None)[0], Path("/root/.config/btrbk_tui/config.json"))

    def test_mounted_subvolumes_come_from_mountinfo(self):
        mountinfo = (
            "23 1 0:21 /@ / rw,relatime shared:1 - btrfs /dev/nvme0n1p2 rw,subvol=/@\n"
            "24 23 0:21 /@home /home rw,relatime shared:2 - btrfs /dev/nvme0n1p2 rw,subvol=/@home\n"
            "25 23 0:21 / /mnt/btr_pool rw,relatime shared:3 - btrfs /dev/nvme0n1p2 rw,subvolid=5\n"
            "26 23 0:22 / /tmp rw shared:4 - tmpfs tmpfs rw\n"
            "27 24 0:21 /home /home rw,relatime shared:5 - btrfs /dev/sda1 rw,subvol=/home\n"
        )
        self.assertEqual(tui.mounted_subvolume(mountinfo, "/"), "@")
        # mounted twice: the later mount hides the earlier one
        self.assertEqual(tui.mounted_subvolume(mountinfo, "/home"), "home")
        self.assertEqual(tui.mounted_subvolume(mountinfo, "/mnt/btr_pool"), "")
        self.assertIsNone(tui.mounted_subvolume(mountinfo, "/tmp"))  # noqa: S108 (a mountpoint, not a file)
        self.assertIsNone(tui.mounted_subvolume(mountinfo, "/var"))

    def test_groups_put_root_first_and_newest_on_top(self):
        groups = tui.group_snapshots([
            "@home.20260801T0000", "@games.20260801T0000", "@.20260801T0000",
            "@home.20260803T0000", "@home.20260802T0000", "not_a_snapshot",
            # a subvolume really called @root is its own group, never "@"
            "@root.20260801T0000",
        ])
        self.assertEqual([prefix for prefix, _ in groups], ["@", "@games", "@home", "@root"])
        self.assertEqual(groups[2][1],
                         ["@home.20260803T0000", "@home.20260802T0000", "@home.20260801T0000"])

    def test_timestamps_in_every_btrbk_format(self):
        cases = {
            "20260803": "2026-08-03T00:00:00",
            "20260803T1405": "2026-08-03T14:05:00",
            "20260803T1405_2": "2026-08-03T14:05:00",
            "20260803T140559+0200": "2026-08-03T14:05:59",
            "20260803T140559-0500_1": "2026-08-03T14:05:59",
            "20260803_140559": "2026-08-03T14:05:59",
            "BROKEN": None,
            "20261399": None,
        }
        for timestamp, expected in cases.items():
            with self.subTest(timestamp=timestamp):
                parsed = tui.parse_btrbk_timestamp(timestamp)
                self.assertEqual(parsed.isoformat() if parsed else None, expected)


class PurgePlanTest(unittest.TestCase):
    def test_keeps_the_parent_and_everything_newer(self):
        groups = [("@home", [
            ("@home.1", "u0"),
            ("@home.2", "u1"),
            ("@home.3", "u2"),  # newest one on the target: the parent
            ("@home.4", "u3"),
        ])]
        plan = tui.compute_purge_plan(groups, {"u1", "u2"})
        self.assertEqual(plan.delete, ["@home.1", "@home.2"])
        self.assertEqual(plan.skipped, [])

    def test_never_touches_a_broken_chain(self):
        groups = [
            ("@games", [("@games.1", "u1"), ("@games.2", "u2")]),  # nothing in common
            ("@home", [("@home.1", None), ("@home.2", None)]),      # uuid unreadable
            ("@", [("@.1", "u9"), ("@.2", "u4")]),                  # the parent is the oldest
            ("@log", [("@log.1", "u5")]),                           # a single snapshot
        ]
        plan = tui.compute_purge_plan(groups, {"u9"})
        self.assertEqual(plan.delete, [])
        self.assertEqual(plan.skipped, ["@games", "@home"])
        self.assertEqual(tui.compute_purge_plan(groups, set()).delete, [])


class OutputTest(unittest.TestCase):
    def test_lines_lose_ansi_and_control_characters(self):
        self.assertEqual(tui.clean_output_line("\x1b[1;32mdone\x1b[0m"), "done")
        self.assertEqual(tui.clean_output_line("a\tb\x07c"), "a bc")
        self.assertEqual(tui.clean_output_line("già fatto"), "già fatto")

    def test_progress_updates_replace_each_other(self):
        log = tui.OutputLog()
        log.push("Creating snapshot", False)
        log.push("10MiB 0:00:01", True)
        log.push("20MiB 0:00:02", True)
        self.assertEqual(log.lines, ["Creating snapshot", "20MiB 0:00:02"])

        # "\r\n" ends the meter: its last state stays on screen
        self.assertFalse(log.push("", False))
        log.push("next subvolume", False)
        self.assertEqual(log.lines, ["Creating snapshot", "20MiB 0:00:02", "next subvolume"])

    def test_streams_are_split_on_both_line_endings(self):
        splitter = tui.StreamSplitter()
        lines = splitter.feed(b"one\ntwo\rthr") + splitter.feed(b"ee\r\nlast") + splitter.flush()
        self.assertEqual(lines, [("one", False), ("two", True), ("three", True), ("", False), ("last", False)])

    def test_key_char_folds_case_and_ignores_special_keys(self):
        self.assertEqual(tui.key_char(ord("S")), "s")
        self.assertIsNone(tui.key_char(tui.curses.KEY_UP))
        self.assertIsNone(tui.key_char(-1))
        self.assertIsNone(tui.key_char(ord("1")))


if __name__ == "__main__":
    unittest.main()
