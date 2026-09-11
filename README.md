# ErgenOS Secure Boot

Secure Boot configuration for installed ErgenOS systems using a
Microsoft-signed shim and a locally generated Machine Owner Key (MOK).

The project is derived from
[garuda-secureboot](https://gitlab.com/garuda-linux/pkgbuilds/-/tree/main/garuda-secureboot)
and preserves its upstream Git history. The implementation has been redesigned
for the ErgenOS boot layout, GRUB, `mkinitcpio` and package hooks.

## Status

The complete lifecycle has been validated in QEMU/KVM with OVMF and on a
physical Lenovo ThinkPad: MOK enrollment, Secure Boot activation, booting
through shim, kernel and GRUB updates, DKMS signing, protected package
removal, MOK deletion, disabling the feature and returning to the normal
ErgenOS boot entry.

Version 0.2 remains a development release. Firmware implementations differ,
so keep a firmware-accessible recovery path available.
The current signed repository package is `ergenos-secureboot` 0.2.0.dev-2.

The first release targets:

- installed ErgenOS systems on x86_64;
- UEFI firmware;
- GRUB installed by the ErgenOS Calamares configuration;
- an EFI System Partition mounted at `/boot/efi`;
- `linux-zen` and other kernels installed under `/boot/vmlinuz-*`;
- a standalone GRUB EFI image with its required modules and configuration
  embedded, SBAT metadata included and an ErgenOS MOK signature;
- automatic re-signing after kernel, initramfs and GRUB updates.

When DKMS is installed, ErgenOS configures it to use the same enrolled MOK,
rebuilds the modules already present on the system and independently signs and
verifies the installed modules, including compressed `.ko.zst` files. This
covers drivers such as NVIDIA and `broadcom-wl-dkms` without maintaining a
second enrollment key. Run `sudo ergenos-secureboot refresh` after installing
or updating a DKMS driver to force rebuilding and verified re-signing.

It does not change the firmware Platform Key, KEK or `db`, and it does not
require UEFI Setup Mode.

## Boot chain

```text
UEFI Microsoft trust database
    -> Microsoft-signed shimx64.efi
    -> user-enrolled ErgenOS MOK
    -> MOK-signed grubx64.efi
    -> MOK-signed kernel
    -> ErgenOS
```

## Setup guide

The complete end-user guide is available at
[ergenossw.github.io/ErgenOS-Website/secure-boot.html](https://ergenossw.github.io/ErgenOS-Website/secure-boot.html).

The recommended setup method is the guided page in ErgenCTL 1.1. Install both
current packages from the signed ErgenOS repository:

```bash
sudo pacman -Syu ergenctl ergenos-secureboot
```

Open **ErgenCTL → Secure Boot**, select **Check readiness**, and then select
**Set up Secure Boot**. Enter a one-time MOK password twice and approve the
system authentication dialog. ErgenCTL shows the next required step and a
nine-item checklist instead of requiring the user to interpret command output.

Reboot into the `ErgenOS Secure Boot` entry, select **Enroll MOK** in MokManager
and enter the same one-time password. Enable Secure Boot in the firmware's
standard/default-key mode without clearing or replacing its platform keys,
then boot the `ErgenOS Secure Boot` entry again. Return to **ErgenCTL → Secure
Boot** and select **Check status**.

The terminal workflow remains available. Run the non-destructive preflight:

```bash
sudo ergenos-secureboot enable --dry-run
```

Prepare the signed boot chain and request MOK enrollment:

```bash
sudo ergenos-secureboot enable
```

Choose a one-time password when `mokutil` asks for it and complete the same
MokManager and firmware steps described above. Verify the result with:

```bash
sudo ergenos-secureboot finalize
sudo ergenos-secureboot check
mokutil --sb-state
```

Future kernel and GRUB updates are signed automatically by packaged hooks.
After a DKMS driver change, run `sudo ergenos-secureboot refresh` and then
`sudo ergenos-secureboot check`.

## Commands

```text
sudo ergenos-secureboot status [--json]
sudo ergenos-secureboot check [--json]
sudo ergenos-secureboot enable [--dry-run]
sudo ergenos-secureboot finalize
sudo ergenos-secureboot refresh
sudo ergenos-secureboot remove-mok
sudo ergenos-secureboot disable [--keep-mok]
```

The normal `ErgenOS` UEFI entry is retained as a recovery path. The tool does
not delete it while preparing the Secure Boot entry.

Do not remove the package while Secure Boot support is configured. Its
pre-transaction hook deliberately aborts such a removal. Remove the MOK,
disable Secure Boot in firmware, boot the normal `ErgenOS` entry and run
`sudo ergenos-secureboot disable` first.

## Validated QEMU/OVMF matrix

The 2026-09-08 integration test covered:

- ErgenOS 1.0 installed in UEFI mode on Q35/OVMF;
- Microsoft certificates enrolled in the OVMF variable store;
- Fedora shim 16.1 and a 2048-bit ErgenOS MOK;
- `linux-zen`, GRUB and `mkinitcpio` post-update signing;
- `broadcom-wl-dkms` rebuilt and signed by the ErgenOS MOK;
- a reboot with Secure Boot enabled before and after package updates;
- safe MOK removal, feature disablement, package removal and normal boot.

The current ErgenOS ISO itself is not Secure Boot bootable. This tool targets
an already installed ErgenOS system that was initially installed with Secure
Boot disabled.

## Validated physical hardware

The 2026-09-08 physical test covered:

- ErgenOS 1.0 on a Lenovo ThinkPad using its existing UEFI key database;
- MOK enrollment through MokManager followed by firmware Secure Boot
  activation;
- booting the dedicated `ErgenOS Secure Boot` entry through shim;
- signed `linux-zen` and GRUB images;
- rebuilding, explicitly signing and loading the compressed
  `broadcom-wl-dkms` module;
- verification with `mokutil`, `ergenos-secureboot check`, `modinfo` and the
  kernel journal;
- no module signature verification failure after reboot.

This confirms one physical configuration, not universal firmware or hardware
compatibility. Additional hardware reports are welcome.

## Signed shim

The `shim-signed` subdirectory contains the ErgenOS packaging recipe for the
Microsoft-signed shim 16.1 binaries published by Fedora through Koji. The
recipe is based on the AUR `shim-signed` package and verifies checksums,
architecture, Authenticode signatures, SBAT data and Microsoft UEFI CA 2011
and 2023 certificate information.

The Fedora binaries are copied without modification, stripping or re-signing.

## Development checks

```bash
python -m unittest -v test_ergenos_secureboot.py
python -m py_compile ergenos_secureboot.py
```

Firmware-changing integration tests must be performed only in a disposable
QEMU/KVM virtual machine using OVMF and a snapshot.

## License and attribution

The ErgenOS Secure Boot implementation is distributed under
GPL-3.0-or-later, matching the license declared by the Garuda upstream package.
The redistributed shim binaries remain under their upstream BSD license and
copyright. See `THIRD_PARTY_NOTICES.md` and the license installed by the
`shim-signed` package.


## ErgenCTL GUI integration

`capabilities` describes GUI protocol 1 without requesting administrator
privileges. `gui ACTION` is the restricted GUI entry point authenticated by
the packaged Polkit policy. Actions: status, check, preflight, enable,
finalize, refresh, remove-mok and disable. Responses have `schema_version`,
`success`, `action`, `message`, and, when available, a `status` object.

For enable/remove-mok only, the GUI sends one JSON line containing `password`
on stdin. The backend validates 8–16 ASCII letters/digits and passes the
password twice to mokutil on its private stdin, never in argv or environment.
The existing terminal CLI remains available. Captured mokutil output is
intentionally discarded; error reporting must never echo password input.
The stdin behavior is supported by upstream mokutil's `read_hidden_line`:
https://github.com/lcp/mokutil/blob/master/src/util.c .

The status now includes schema version, current shim boot and recorded MOK
requests. Request flags describe successfully submitted operations, not an
independent inspection of firmware request queues. Actual enrollment and
firmware activation are rechecked on each status request. Interrupted setup
is retained as configured so hooks and diagnostics can detect/repair it.
Unreadable state is an error, and a busy lock fails without waiting forever.
Disabling support requires confirmed disabled firmware and the normal boot
entry. The terminal CLI uses the same implementation and remains available for
automation, diagnostics and recovery.
