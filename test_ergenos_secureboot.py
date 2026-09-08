from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ergenos_secureboot as secureboot


class EfibootmgrTests(unittest.TestCase):
    def test_parse_current_order_and_nvme_entry(self) -> None:
        output = """BootCurrent: 0007
BootOrder: 0007,0002,0000
Boot0002* ErgenOS\tHD(1,GPT,abc,0x800,0x100000)/File(\\EFI\\ErgenOS\\grubx64.efi)
Boot0007* ErgenOS Secure Boot\tHD(1,GPT,abc,0x800,0x100000)/File(\\EFI\\ErgenOS-SecureBoot\\shimx64.efi)
"""
        current, order, entries = secureboot.parse_efibootmgr(output)
        self.assertEqual(current, 7)
        self.assertEqual(order, (7, 2, 0))
        self.assertEqual(entries[0].label, "ErgenOS")
        self.assertEqual(entries[1].number, 7)
        self.assertEqual(entries[1].loader, r"\EFI\ErgenOS-SecureBoot\shimx64.efi")

    def test_parse_entry_without_active_marker(self) -> None:
        _, _, entries = secureboot.parse_efibootmgr(
            "Boot000A  UEFI OS\tHD(1,GPT,id,0x800,0x1000)/File(\\EFI\\BOOT\\BOOTX64.EFI)"
        )
        self.assertEqual(entries[0].number, 10)
        self.assertEqual(entries[0].label, "UEFI OS")

    def test_parse_entry_with_spaces_instead_of_tab(self) -> None:
        _, _, entries = secureboot.parse_efibootmgr(
            "Boot0007* ErgenOS Secure Boot    HD(1,GPT,id,0x800,0x1000)/File(\\EFI\\ErgenOS-SecureBoot\\shimx64.efi)"
        )
        self.assertEqual(entries[0].label, secureboot.BOOT_LABEL)
        self.assertTrue(secureboot.expected_secure_boot_entry(entries[0]))

    def test_same_label_with_wrong_loader_is_rejected(self) -> None:
        entry = secureboot.BootEntry(
            7, secureboot.BOOT_LABEL, r"\EFI\somewhere-else\shimx64.efi"
        )
        self.assertFalse(secureboot.expected_secure_boot_entry(entry))

    def test_parse_current_efibootmgr_loader_without_file_wrapper(self) -> None:
        output = (
            "Boot0004* ErgenOS Secure Boot\t"
            "HD(1,GPT,id,0x1000,0x200000)/\\EFI\\ErgenOS-SecureBoot\\shimx64.efi"
        )
        _, _, entries = secureboot.parse_efibootmgr(output)
        self.assertEqual(entries[0].loader, secureboot.BOOT_LOADER)
        self.assertTrue(secureboot.expected_secure_boot_entry(entries[0]))


class StateTests(unittest.TestCase):
    def test_mokutil_zero_exit_does_not_mean_key_is_enrolled(self) -> None:
        result = secureboot.CommandResult(
            0, "/var/lib/ergenos/secureboot/keys/MOK.cer is not enrolled", ""
        )
        with tempfile.TemporaryDirectory() as directory:
            certificate = Path(directory) / "MOK.cer"
            certificate.touch()
            with (
                patch.object(secureboot, "MOK_CER", certificate),
                patch.object(secureboot, "run", return_value=result),
            ):
                self.assertFalse(secureboot.mok_is_enrolled())

    def test_mokutil_enrolled_message_is_accepted(self) -> None:
        result = secureboot.CommandResult(
            1, "/var/lib/ergenos/secureboot/keys/MOK.cer is already enrolled", ""
        )
        with tempfile.TemporaryDirectory() as directory:
            certificate = Path(directory) / "MOK.cer"
            certificate.touch()
            with (
                patch.object(secureboot, "MOK_CER", certificate),
                patch.object(secureboot, "run", return_value=result),
            ):
                self.assertTrue(secureboot.mok_is_enrolled())

    def status_with(self, *, configured: bool, enrolled: bool,
                    secure_boot: str, signed: bool = True) -> secureboot.SystemStatus:
        with (
            patch.object(secureboot, "load_state", return_value={"configured": configured}),
            patch.object(secureboot.Path, "exists", return_value=True),
            patch.object(secureboot, "secure_boot_state", return_value=secure_boot),
            patch.object(secureboot, "mok_is_enrolled", return_value=enrolled),
            patch.object(secureboot, "signed_by_our_mok", return_value=signed),
            patch.object(secureboot, "shim_is_valid", return_value=True),
            patch.object(secureboot, "installed_kernels", return_value=[Path("/boot/vmlinuz-linux-zen")]),
            patch.object(secureboot, "boot_entries", return_value=(1, (1,), [
                secureboot.BootEntry(1, secureboot.BOOT_LABEL, r"\EFI\ErgenOS-SecureBoot\shimx64.efi")
            ])),
            patch.object(secureboot.Path, "is_file", return_value=True),
        ):
            return secureboot.collect_status()

    def test_unconfigured_state(self) -> None:
        self.assertEqual(
            self.status_with(configured=False, enrolled=False, secure_boot="disabled").state,
            "unconfigured",
        )

    def test_enrollment_pending_state(self) -> None:
        self.assertEqual(
            self.status_with(configured=True, enrolled=False, secure_boot="disabled").state,
            "enrollment-pending",
        )

    def test_configured_but_firmware_disabled(self) -> None:
        self.assertEqual(
            self.status_with(configured=True, enrolled=True, secure_boot="disabled").state,
            "configured",
        )

    def test_active_state(self) -> None:
        self.assertEqual(
            self.status_with(configured=True, enrolled=True, secure_boot="enabled").state,
            "active",
        )

    def test_degraded_state_has_problem(self) -> None:
        status = self.status_with(
            configured=True, enrolled=True, secure_boot="enabled", signed=False
        )
        self.assertEqual(status.state, "degraded")
        self.assertTrue(status.problems)


class FileOperationTests(unittest.TestCase):
    def test_duplicate_secure_boot_entry_is_removed(self) -> None:
        entries = [
            secureboot.BootEntry(2, secureboot.BOOT_LABEL, secureboot.BOOT_LOADER),
            secureboot.BootEntry(4, secureboot.BOOT_LABEL, secureboot.BOOT_LOADER),
        ]
        with (
            patch.object(secureboot, "boot_entries", return_value=(4, (4, 2), entries)),
            patch.object(secureboot, "run_checked") as run_checked,
        ):
            secureboot.ensure_boot_entry()
        run_checked.assert_called_once_with(["efibootmgr", "-b", "0002", "-B"])

    def test_atomic_copy_replaces_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            destination = root / "subdirectory" / "destination"
            source.write_bytes(b"signed")
            destination.parent.mkdir()
            destination.write_bytes(b"old")
            secureboot.atomic_copy(source, destination)
            self.assertEqual(destination.read_bytes(), b"signed")
            self.assertFalse(destination.with_name(".destination.ergenos-secureboot.tmp").exists())

    def test_incomplete_key_material_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            key_dir = Path(directory)
            key = key_dir / "MOK.key"
            key.write_text("existing", encoding="utf-8")
            with (
                patch.object(secureboot, "KEY_DIR", key_dir),
                patch.object(secureboot, "MOK_KEY", key),
                patch.object(secureboot, "MOK_CRT", key_dir / "MOK.crt"),
                patch.object(secureboot, "MOK_CER", key_dir / "MOK.cer"),
            ):
                with self.assertRaises(secureboot.SecureBootError):
                    secureboot.generate_mok()
            self.assertEqual(key.read_text(encoding="utf-8"), "existing")

    def test_kernel_hook_does_not_resign_an_already_signed_kernel(self) -> None:
        kernel = Path("/boot/vmlinuz-linux-zen")
        with (
            patch.object(secureboot, "require_root"),
            patch.object(secureboot, "load_state", return_value={"configured": True}),
            patch.object(secureboot, "signed_by_our_mok", return_value=True),
            patch.object(secureboot, "sign_file") as sign_file,
        ):
            secureboot.sign_kernel_from_hook(kernel)
        sign_file.assert_not_called()

    def test_dkms_configuration_uses_the_ergenos_mok(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "etc/dkms/framework.conf.d/99-ergenos-secureboot.conf"
            key = root / "MOK.key"
            certificate = root / "MOK.crt"
            with (
                patch.object(secureboot, "DKMS_CONFIG", config),
                patch.object(secureboot, "MOK_KEY", key),
                patch.object(secureboot, "MOK_CRT", certificate),
                patch.object(secureboot.shutil, "which", return_value=None),
            ):
                secureboot.configure_dkms()
            content = config.read_text(encoding="utf-8")
            self.assertIn(f'mok_signing_key="{key}"', content)
            self.assertIn(f'mok_certificate="{certificate}"', content)
            self.assertIn('try_sign_modules="true"', content)


if __name__ == "__main__":
    unittest.main()
