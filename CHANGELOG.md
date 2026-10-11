# Changelog

## 0.2.0.dev-6

Restore the ErgenOS Secure Boot UEFI entry during GRUB recovery.

## 0.2.0.dev-5

Load the current on-disk GRUB configuration from the signed loader, preserving
the dynamic snapshot menu. Include the required modules and trusted Unicode font.

## Earlier 0.2 development work

- Add the ErgenCTL GUI protocol for guided setup and maintenance.
- Sign and verify installed DKMS modules, including compressed .ko.zst modules.
- Validate the lifecycle under QEMU/OVMF and on a physical Lenovo ThinkPad.

## ErgenOS 1.2 documentation update

No new backend version is introduced by this documentation change. Historical
ThinkPad/DKMS tests do not establish NVIDIA Secure Boot validation on the Acer.
Installation media still requires Secure Boot disabled; enable it after install.
