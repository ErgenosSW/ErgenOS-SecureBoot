# shim-signed for ErgenOS

This package redistributes the Microsoft-signed x86_64 shim, MokManager and
fallback binaries published by Fedora through Koji. It is based on the Arch
User Repository `shim-signed` packaging recipe.

The downloaded Fedora RPM and the upstream shim license are pinned by SHA-512.
The package check verifies the PE architecture, SBAT sections, Authenticode
signatures and the presence of the Microsoft UEFI CA 2011 and 2023 certificate
chains before packaging.

The files are not modified, stripped or re-signed by ErgenOS.
