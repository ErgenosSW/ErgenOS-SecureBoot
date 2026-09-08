# Third-party notices

## garuda-secureboot

ErgenOS Secure Boot is derived from `garuda-secureboot` by Garuda Linux.

- Upstream: https://gitlab.com/garuda-linux/pkgbuilds/-/tree/main/garuda-secureboot
- Original maintainer: TNE, Garuda Linux
- Declared license: GPL-3.0-or-later

The original commits and authorship are preserved in this repository's Git
history. The ErgenOS implementation changes distribution paths, bootloader
handling, initramfs integration, state management and update hooks.

## shim

The `shim-signed` package redistributes unmodified Microsoft-signed shim,
MokManager and fallback binaries built and published by Fedora.

- Project: https://github.com/rhboot/shim
- Binary source: https://koji.fedoraproject.org/koji/packageinfo?packageID=14502
- Packaging reference: https://aur.archlinux.org/packages/shim-signed
- Copyright: Red Hat, Inc. and shim contributors
- License: BSD-2-Clause

The upstream copyright and license text is installed at
`/usr/share/licenses/shim-signed/COPYRIGHT`.
