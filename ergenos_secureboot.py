#!/usr/bin/env python3

"""Secure Boot management for installed ErgenOS systems."""

from __future__ import annotations

import argparse
import fcntl
import getpass
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


PROGRAM = "ergenos-secureboot"
EFI_MOUNT = Path("/boot/efi")
EFI_VENDOR_DIR = EFI_MOUNT / "EFI" / "ErgenOS-SecureBoot"
NORMAL_EFI_DIR = EFI_MOUNT / "EFI" / "ErgenOS"
SHIM_SOURCE_DIR = Path("/usr/share/shim-signed")
DATA_DIR = Path("/var/lib/ergenos/secureboot")
KEY_DIR = DATA_DIR / "keys"
BACKUP_DIR = DATA_DIR / "backups"
STATE_FILE = DATA_DIR / "state.json"
LOCK_FILE = Path("/run/lock/ergenos-secureboot.lock")
DKMS_CONFIG = Path("/etc/dkms/framework.conf.d/99-ergenos-secureboot.conf")
MOK_KEY = KEY_DIR / "MOK.key"
MOK_CRT = KEY_DIR / "MOK.crt"
MOK_CER = KEY_DIR / "MOK.cer"
BOOT_LABEL = "ErgenOS Secure Boot"
NORMAL_BOOT_LABEL = "ErgenOS"
BOOT_LOADER = r"\EFI\ErgenOS-SecureBoot\shimx64.efi"
NORMAL_BOOT_LOADER = r"\EFI\ErgenOS\grubx64.efi"
GRUB_MODULES = (
    "all_video boot btrfs cat chain configfile cryptodisk echo efifwsetup efinet "
    "ext2 fat font gettext gfxmenu gfxterm gfxterm_background gzio halt help "
    "hfsplus iso9660 jpeg keystatus loadenv loopback linux lsefi lsefimmap "
    "lsefisystab lssal luks luks2 lvm memdisk minicmd normal ntfs part_apple "
    "part_gpt part_msdos password_pbkdf2 png probe reboot regexp search "
    "search_fs_file search_fs_uuid search_label sleep smbios squash4 test tpm "
    "true video xfs zfs zfscrypt zfsinfo"
)


class SecureBootError(RuntimeError):
    """An expected, user-facing Secure Boot error."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class BootEntry:
    number: int
    label: str
    loader: str


@dataclass(frozen=True)
class SystemStatus:
    state: str
    uefi: bool
    secure_boot: str
    configured: bool
    mok_created: bool
    mok_enrolled: bool
    shim_installed: bool
    grub_installed: bool
    grub_signed: bool
    unsigned_kernels: tuple[str, ...]
    boot_entry: bool
    problems: tuple[str, ...]


def run(argv: Sequence[str], *, timeout: int | None = 30,
        input_text: str | None = None, inherit_stdio: bool = False) -> CommandResult:
    try:
        if inherit_stdio:
            completed = subprocess.run(list(argv), timeout=timeout, check=False)
            return CommandResult(completed.returncode, "", "")
        completed = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=timeout,
            input=input_text, check=False,
        )
        return CommandResult(
            completed.returncode, completed.stdout.strip(), completed.stderr.strip()
        )
    except FileNotFoundError as error:
        return CommandResult(127, "", f"Command not found: {error.filename}")
    except subprocess.TimeoutExpired:
        return CommandResult(124, "", f"Command timed out: {argv[0]}")


def run_checked(argv: Sequence[str], *, timeout: int | None = 30) -> CommandResult:
    result = run(argv, timeout=timeout)
    if result.returncode != 0:
        detail = result.stderr or result.stdout or f"exit status {result.returncode}"
        raise SecureBootError(f"{' '.join(argv)} failed: {detail}")
    return result


def require_root() -> None:
    if os.geteuid() != 0:
        raise SecureBootError("This operation must be run as root.")


def require_command(name: str) -> None:
    if shutil.which(name) is None:
        raise SecureBootError(f"Required command is not installed: {name}")


def load_state() -> dict[str, object]:
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def write_state(**updates: object) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    current = load_state()
    current.update(updates)
    current["updated_at"] = datetime.now(timezone.utc).isoformat()
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, STATE_FILE)


def secure_boot_state() -> str:
    result = run(["mokutil", "--sb-state"])
    output = f"{result.stdout}\n{result.stderr}".lower()
    if "secureboot enabled" in output:
        return "enabled"
    if "secureboot disabled" in output:
        return "disabled"
    return "unknown"


def mok_is_enrolled() -> bool:
    if not MOK_CER.is_file():
        return False
    result = run(["mokutil", "--test-key", str(MOK_CER)])
    output = f"{result.stdout}\n{result.stderr}".casefold()
    return (
        "is not enrolled" not in output
        and re.search(r"\bis (?:already )?enrolled\b", output) is not None
    )


def parse_efibootmgr(output: str) -> tuple[int | None, tuple[int, ...], list[BootEntry]]:
    current: int | None = None
    order: tuple[int, ...] = ()
    entries: list[BootEntry] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        match = re.match(r"BootCurrent:\s*([0-9A-Fa-f]{4})", line)
        if match:
            current = int(match.group(1), 16)
            continue
        match = re.match(r"BootOrder:\s*(.*)", line)
        if match:
            values = [value for value in match.group(1).split(",") if value]
            order = tuple(int(value, 16) for value in values)
            continue
        match = re.match(
            r"Boot([0-9A-Fa-f]{4})\*?\s+(.+?)\s+(?=(?:HD|File|VenHw|BBS)\()(.*)$",
            line,
        )
        if not match:
            continue
        details = match.group(3) or ""
        loader_match = re.search(r"File\(([^)]+)\)", details, re.IGNORECASE)
        if not loader_match:
            loader_match = re.search(r"(\\EFI\\\S+)", details, re.IGNORECASE)
        entries.append(BootEntry(
            number=int(match.group(1), 16),
            label=match.group(2).strip(),
            loader=loader_match.group(1) if loader_match else "",
        ))
    return current, order, entries


def boot_entries() -> tuple[int | None, tuple[int, ...], list[BootEntry]]:
    result = run(["efibootmgr", "-v"])
    if result.returncode != 0:
        return None, (), []
    return parse_efibootmgr(result.stdout)


def signed_by_our_mok(path: Path) -> bool:
    if not path.is_file() or not MOK_CRT.is_file():
        return False
    return run(["sbverify", "--cert", str(MOK_CRT), str(path)]).returncode == 0


def normalized_loader(loader: str) -> str:
    return loader.replace("/", "\\").casefold()


def expected_secure_boot_entry(entry: BootEntry) -> bool:
    return (
        entry.label.casefold() == BOOT_LABEL.casefold()
        and normalized_loader(entry.loader) == normalized_loader(BOOT_LOADER)
    )


def shim_is_valid(path: Path) -> bool:
    if not path.is_file():
        return False
    signature = run(["sbverify", "--list", str(path)])
    sections = run(["objdump", "-h", str(path)])
    combined = f"{signature.stdout}\n{signature.stderr}"
    return (
        signature.returncode == 0
        and sections.returncode == 0
        and ".sbat" in sections.stdout
        and "Microsoft Corporation UEFI CA 2011" in combined
        and "Microsoft UEFI CA 2023" in combined
    )


def installed_kernels() -> list[Path]:
    return sorted(path for path in Path("/boot").glob("vmlinuz-*") if path.is_file())


def collect_status() -> SystemStatus:
    state_data = load_state()
    configured = bool(state_data.get("configured", False))
    uefi = Path("/sys/firmware/efi").exists()
    sb_state = secure_boot_state() if uefi else "unavailable"
    mok_created = all(path.is_file() for path in (MOK_KEY, MOK_CRT, MOK_CER))
    enrolled = mok_is_enrolled() if uefi else False
    shim = shim_is_valid(EFI_VENDOR_DIR / "shimx64.efi")
    grub_path = EFI_VENDOR_DIR / "grubx64.efi"
    grub = grub_path.is_file()
    grub_signed = signed_by_our_mok(grub_path)
    unsigned = (
        tuple(str(path) for path in installed_kernels() if not signed_by_our_mok(path))
        if configured else ()
    )
    _, _, entries = boot_entries() if uefi else (None, (), [])
    entry_found = any(expected_secure_boot_entry(entry) for entry in entries)

    problems: list[str] = []
    if configured:
        if not mok_created:
            problems.append("MOK key material is incomplete")
        if not shim:
            problems.append("shimx64.efi is missing or failed signature/SBAT validation")
        if not grub_signed:
            problems.append("GRUB is missing or does not have the ErgenOS MOK signature")
        if unsigned:
            problems.append("one or more installed kernels are not signed")
        if not entry_found:
            problems.append("the ErgenOS Secure Boot UEFI entry is missing")

    if not configured:
        state = "unconfigured"
    elif problems:
        state = "degraded"
    elif not enrolled:
        state = "enrollment-pending"
    elif sb_state == "enabled":
        state = "active"
    else:
        state = "configured"

    return SystemStatus(
        state=state, uefi=uefi, secure_boot=sb_state, configured=configured,
        mok_created=mok_created, mok_enrolled=enrolled, shim_installed=shim,
        grub_installed=grub, grub_signed=grub_signed,
        unsigned_kernels=unsigned, boot_entry=entry_found,
        problems=tuple(problems),
    )


def preflight() -> None:
    if not Path("/sys/firmware/efi").exists():
        raise SecureBootError("ErgenOS was not booted in UEFI mode.")
    for command in (
        "efibootmgr", "findmnt", "grub-mkstandalone", "mokutil", "openssl",
        "objdump", "sbsign", "sbverify",
    ):
        require_command(command)
    if not EFI_MOUNT.is_mount():
        raise SecureBootError("The EFI System Partition is not mounted at /boot/efi.")
    filesystem = run_checked(["findmnt", "-n", "-o", "FSTYPE", str(EFI_MOUNT)]).stdout
    if filesystem.casefold() not in {"vfat", "fat", "fat32"}:
        raise SecureBootError(f"/boot/efi is not a FAT filesystem (found {filesystem}).")
    if not os.access(EFI_MOUNT, os.W_OK):
        raise SecureBootError("The EFI System Partition is not writable.")
    if not Path("/boot/grub/grub.cfg").is_file():
        raise SecureBootError("ErgenOS GRUB configuration was not found.")
    if not Path("/usr/share/grub/sbat.csv").is_file():
        raise SecureBootError("The GRUB SBAT metadata file is missing.")
    for filename in ("shimx64.efi", "mmx64.efi"):
        if not SHIM_SOURCE_DIR.joinpath(filename).is_file():
            raise SecureBootError(f"Signed shim component is missing: {filename}")
    if not shim_is_valid(SHIM_SOURCE_DIR / "shimx64.efi"):
        raise SecureBootError("The installed shim failed signature or SBAT validation.")


def generate_mok() -> None:
    if all(path.is_file() for path in (MOK_KEY, MOK_CRT, MOK_CER)):
        return
    if any(path.exists() for path in (MOK_KEY, MOK_CRT, MOK_CER)):
        raise SecureBootError("Incomplete MOK key material exists; refusing to overwrite it.")
    KEY_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(KEY_DIR, 0o700)
    run_checked([
        "openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", str(MOK_KEY),
        "-new", "-x509", "-sha256", "-days", "3650",
        "-subj", "/CN=ErgenOS Machine Owner Key/", "-out", str(MOK_CRT),
    ], timeout=None)
    os.chmod(MOK_KEY, 0o600)
    os.chmod(MOK_CRT, 0o644)
    run_checked([
        "openssl", "x509", "-outform", "DER", "-in", str(MOK_CRT),
        "-out", str(MOK_CER),
    ])
    os.chmod(MOK_CER, 0o644)


def configure_dkms() -> None:
    DKMS_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "# Managed by ergenos-secureboot.\n"
        "try_sign_modules=\"true\"\n"
        f"mok_signing_key=\"{MOK_KEY}\"\n"
        f"mok_certificate=\"{MOK_CRT}\"\n"
    )
    temporary = DKMS_CONFIG.with_suffix(".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.chmod(temporary, 0o644)
    os.replace(temporary, DKMS_CONFIG)

    if shutil.which("dkms") is not None:
        print("Rebuilding installed DKMS modules with the ErgenOS MOK")
        run_checked(["dkms", "autoinstall", "--force"], timeout=None)


def sign_file(path: Path) -> None:
    if not path.is_file():
        raise SecureBootError(f"Cannot sign missing file: {path}")
    if not MOK_KEY.is_file() or not MOK_CRT.is_file():
        raise SecureBootError("ErgenOS MOK signing key is unavailable.")
    temporary = path.with_name(f".{path.name}.ergenos-secureboot.tmp")
    try:
        run_checked([
            "sbsign", "--key", str(MOK_KEY), "--cert", str(MOK_CRT),
            "--output", str(temporary), str(path),
        ], timeout=None)
        if not signed_by_our_mok(temporary):
            raise SecureBootError(f"Signature verification failed for {path}")
        mode = stat.S_IMODE(path.stat().st_mode)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def sign_all_kernels() -> None:
    kernels = installed_kernels()
    if not kernels:
        raise SecureBootError("No installed kernels were found in /boot.")
    for kernel in kernels:
        if not signed_by_our_mok(kernel):
            print(f"Signing {kernel}")
            sign_file(kernel)


def backup_efi_files() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    destination = BACKUP_DIR / timestamp
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    for directory in (NORMAL_EFI_DIR, EFI_VENDOR_DIR):
        if directory.exists():
            shutil.copytree(directory, destination / directory.name)
    result = run(["efibootmgr", "-v"])
    destination.joinpath("efibootmgr.txt").write_text(
        result.stdout + "\n", encoding="utf-8"
    )
    return destination


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.ergenos-secureboot.tmp")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def refresh_grub() -> None:
    preflight()
    backup_efi_files()
    DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = DATA_DIR / f"grubx64-{os.getpid()}.efi"
    try:
        run_checked([
            "grub-mkstandalone", "--format=x86_64-efi",
            f"--install-modules={GRUB_MODULES}",
            "--sbat", "/usr/share/grub/sbat.csv",
            "--output", str(temporary),
            "boot/grub/grub.cfg=/boot/grub/grub.cfg",
        ], timeout=None)
        if not temporary.is_file():
            raise SecureBootError("grub-mkstandalone did not create grubx64.efi.")
        section_check = run(["objdump", "-h", str(temporary)])
        if section_check.returncode != 0 or ".sbat" not in section_check.stdout:
            raise SecureBootError("The generated GRUB image has no SBAT section.")
        sign_file(temporary)
        atomic_copy(temporary, EFI_VENDOR_DIR / "grubx64.efi")
    finally:
        temporary.unlink(missing_ok=True)
    atomic_copy(SHIM_SOURCE_DIR / "shimx64.efi", EFI_VENDOR_DIR / "shimx64.efi")
    atomic_copy(SHIM_SOURCE_DIR / "mmx64.efi", EFI_VENDOR_DIR / "mmx64.efi")
    atomic_copy(MOK_CER, EFI_VENDOR_DIR / "MOK.cer")


def esp_device_and_partition() -> tuple[str, str]:
    source = run_checked(["findmnt", "-n", "-o", "SOURCE", str(EFI_MOUNT)]).stdout
    result = run_checked(["lsblk", "-n", "-o", "PKNAME,PARTN", source]).stdout
    fields = result.split()
    if len(fields) != 2 or not fields[1].isdigit():
        raise SecureBootError(f"Cannot determine the ESP disk and partition from {source}.")
    return f"/dev/{fields[0]}", fields[1]


def ensure_boot_entry() -> None:
    _, order, entries = boot_entries()
    matching = [entry for entry in entries if expected_secure_boot_entry(entry)]
    if matching:
        numbers = {entry.number for entry in matching}
        preferred = next((number for number in order if number in numbers), matching[0].number)
        for entry in matching:
            if entry.number != preferred:
                run_checked(["efibootmgr", "-b", f"{entry.number:04X}", "-B"])
        return
    disk, partition = esp_device_and_partition()
    run_checked([
        "efibootmgr", "-c", "-d", disk, "-p", partition, "-L", BOOT_LABEL,
        "-l", BOOT_LOADER,
    ])


def request_mok_removal() -> None:
    if not mok_is_enrolled():
        print("The ErgenOS MOK is not enrolled.")
        return
    print("\nChoose a one-time password to authorize removal in MokManager.")
    result = run(["mokutil", "--delete", str(MOK_CER)], timeout=None, inherit_stdio=True)
    if result.returncode != 0:
        raise SecureBootError("mokutil did not queue the ErgenOS MOK for removal.")
    write_state(configured=True, removal_requested=True)
    print("Reboot through 'ErgenOS Secure Boot' and confirm key removal in MokManager.")


def request_mok_enrollment() -> None:
    if mok_is_enrolled():
        return
    print("\nChoose a one-time password when mokutil asks for it.")
    print("You will enter the same password in MokManager after reboot.\n")
    result = run(["mokutil", "--import", str(MOK_CER)], timeout=None, inherit_stdio=True)
    if result.returncode != 0:
        raise SecureBootError("mokutil did not queue the ErgenOS MOK for enrollment.")


def enable(*, dry_run: bool) -> None:
    preflight()
    if dry_run:
        print("Preflight passed. ErgenOS Secure Boot can be configured.")
        return
    generate_mok()
    configure_dkms()
    refresh_grub()
    sign_all_kernels()
    ensure_boot_entry()
    request_mok_enrollment()
    write_state(configured=True, enrollment_requested=not mok_is_enrolled())
    print("\nSecure Boot files are prepared and the MOK enrollment is queued.")
    print("Reboot into 'ErgenOS Secure Boot' and complete enrollment in MokManager.")


def finalize() -> None:
    require_root()
    status = collect_status()
    if not status.configured:
        raise SecureBootError("ErgenOS Secure Boot has not been configured.")
    if not status.mok_enrolled:
        raise SecureBootError("The ErgenOS MOK has not been enrolled yet.")
    if status.problems:
        raise SecureBootError("Configuration is incomplete: " + "; ".join(status.problems))
    write_state(configured=True, enrollment_requested=False, finalized=True)
    if status.secure_boot == "enabled":
        print("ErgenOS Secure Boot is active and verified.")
    else:
        print("MOK enrollment is complete. Enable Secure Boot in the firmware to activate it.")


def disable(*, keep_mok: bool) -> None:
    require_root()
    status = collect_status()
    if not status.configured:
        print("ErgenOS Secure Boot is not configured.")
        return
    if status.secure_boot == "enabled":
        raise SecureBootError(
            "Disable Secure Boot in the firmware and boot the normal ErgenOS entry first."
        )
    if status.mok_enrolled and not keep_mok:
        raise SecureBootError(
            "The ErgenOS MOK is still enrolled. Run 'ergenos-secureboot remove-mok', "
            "reboot through MokManager, then disable support. Use --keep-mok only if "
            "you intentionally want to retain the enrolled key."
        )

    _, order, entries = boot_entries()
    normal = [
        entry.number for entry in entries
        if entry.label.casefold() == NORMAL_BOOT_LABEL.casefold()
        and normalized_loader(entry.loader) == normalized_loader(NORMAL_BOOT_LOADER)
    ]
    secure = [entry.number for entry in entries if expected_secure_boot_entry(entry)]
    if not normal:
        raise SecureBootError("The normal ErgenOS UEFI entry is missing; refusing to disable support.")
    new_order = [normal[0]]
    new_order.extend(number for number in order if number not in {*normal, *secure})
    new_order.extend(number for number in normal[1:] if number not in new_order)
    run_checked(["efibootmgr", "-o", ",".join(f"{number:04X}" for number in new_order)])
    for number in secure:
        run_checked(["efibootmgr", "-b", f"{number:04X}", "-B"])
    DKMS_CONFIG.unlink(missing_ok=True)
    write_state(configured=False, disabled=True)
    print("ErgenOS Secure Boot support has been disabled. Key material and EFI backups were retained.")


def refresh(*, grub_only: bool = False) -> None:
    require_root()
    if not bool(load_state().get("configured", False)):
        return
    preflight()
    if grub_only:
        refresh_grub()
    else:
        refresh_grub()
        sign_all_kernels()
    write_state(configured=True)


def sign_kernel_from_hook(path: Path) -> None:
    require_root()
    if not bool(load_state().get("configured", False)):
        return
    if not signed_by_our_mok(path):
        sign_file(path)


def print_status(status: SystemStatus, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(asdict(status), indent=2))
        return
    labels = (
        ("State", status.state),
        ("UEFI", "yes" if status.uefi else "no"),
        ("Firmware Secure Boot", status.secure_boot),
        ("MOK created", "yes" if status.mok_created else "no"),
        ("MOK enrolled", "yes" if status.mok_enrolled else "no"),
        ("Signed shim installed", "yes" if status.shim_installed else "no"),
        ("GRUB signed", "yes" if status.grub_signed else "no"),
        ("UEFI entry", "yes" if status.boot_entry else "no"),
    )
    print("ErgenOS Secure Boot")
    print("--------------------")
    for label, value in labels:
        print(f"{label:<24} {value}")
    if status.unsigned_kernels:
        print("Unsigned kernels:")
        for path in status.unsigned_kernels:
            print(f"  - {path}")
    if status.problems:
        print("Problems:")
        for problem in status.problems:
            print(f"  - {problem}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Secure Boot on ErgenOS.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    status_parser = subparsers.add_parser("status", help="Show Secure Boot status")
    status_parser.add_argument("--json", action="store_true")
    check_parser = subparsers.add_parser("check", help="Run configuration checks")
    check_parser.add_argument("--json", action="store_true")
    enable_parser = subparsers.add_parser("enable", help="Prepare shim and MOK")
    enable_parser.add_argument("--dry-run", action="store_true")
    subparsers.add_parser("finalize", help="Verify enrollment after reboot")
    subparsers.add_parser("refresh", help="Rebuild and sign GRUB and kernels")
    disable_parser = subparsers.add_parser("disable", help="Return to the normal ErgenOS boot entry")
    disable_parser.add_argument("--keep-mok", action="store_true")
    subparsers.add_parser("remove-mok", help="Queue removal of the ErgenOS MOK")
    subparsers.add_parser("refresh-grub", help="Internal hook: rebuild and sign GRUB")
    hook_parser = subparsers.add_parser("sign-kernel", help="Internal hook: sign one kernel")
    hook_parser.add_argument("path", type=Path)
    return parser


def locked() -> object:
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCK_FILE.open("w", encoding="utf-8")
    fcntl.flock(handle, fcntl.LOCK_EX)
    return handle


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command in {"status", "check"}:
            require_root()
            status = collect_status()
            print_status(status, as_json=args.json)
            return 1 if args.command == "check" and status.problems else 0
        require_root()
        with locked():
            if args.command == "enable":
                enable(dry_run=args.dry_run)
            elif args.command == "finalize":
                finalize()
            elif args.command == "refresh":
                refresh()
            elif args.command == "refresh-grub":
                refresh(grub_only=True)
            elif args.command == "sign-kernel":
                sign_kernel_from_hook(args.path)
            elif args.command == "remove-mok":
                request_mok_removal()
            elif args.command == "disable":
                disable(keep_mok=args.keep_mok)
        return 0
    except SecureBootError as error:
        print(f"{PROGRAM}: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(f"{PROGRAM}: cancelled", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
