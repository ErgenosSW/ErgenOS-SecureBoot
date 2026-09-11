# Maintainer: ErgenOS Project <https://github.com/ErgenosSW>

pkgname=ergenos-secureboot
pkgver=0.2.0.dev
pkgrel=2
pkgdesc='Shim and Machine Owner Key Secure Boot management for ErgenOS'
arch=('x86_64')
url='https://github.com/ErgenosSW/ErgenOS-SecureBoot'
license=('GPL-3.0-or-later')
depends=(
  'binutils'
  'efibootmgr'
  'grub'
  'kmod'
  'mkinitcpio'
  'mokutil'
  'openssl'
  'python'
  'polkit'
  'sbsigntools'
  'shim-signed'
  'util-linux'
  'zstd'
)
optdepends=('dkms: automatically sign out-of-tree kernel modules')
install=ergenos-secureboot.install
source=(
  'ergenos_secureboot.py'
  'ergenos-secureboot.install'
  'ergenos-secureboot-initcpio'
  '95-ergenos-secureboot-grub.hook'
  '10-ergenos-secureboot-remove-guard.hook'
  'removal-guard'
  'test_ergenos_secureboot.py'
  'io.github.ergenossw.ErgenOS.SecureBoot.policy'
)
sha256sums=(
  '8558a7df5981bb8a68d43c1999f02fc476f1c7efb2063b71ed9f0c3ab935f0d2'
  'a06dbd5e3bc67d2bd564b3d10700af8592df51dd5267a469b701a3b8dcc3c5ec'
  '25db5f4d52ac43664fab2666cb8815afbbec727e9c852f733dfd1d00502d4a0a'
  'b98191f0355366554fc759cde1a6fb2b123b46c8f4533899baa8f3851d631c35'
  'fd5ce8481264cac9845f9f4af59f460da19e6a3e4b7e5dfb89386dee1ab9bfb1'
  '805584f6acc125b15077df2ed2203994784c2a829891e1074a011769202d68f9'
  'bc8e6592437ed043c95968fe4e610f0c5638c25fe7bbbfaa63efffead2101c5d'
  '478966750f038a31594a871d72cb938180862ea4f306345a39c6a196c8cffe78'
)

check() {
  PYTHONDONTWRITEBYTECODE=1 python -m unittest -v test_ergenos_secureboot.py
  python -m py_compile ergenos_secureboot.py
}

package() {
  install -Dm644 io.github.ergenossw.ErgenOS.SecureBoot.policy \
    "${pkgdir}/usr/share/polkit-1/actions/io.github.ergenossw.ErgenOS.SecureBoot.policy"
  install -Dm755 ergenos_secureboot.py "${pkgdir}/usr/bin/ergenos-secureboot"
  install -Dm755 ergenos-secureboot-initcpio \
    "${pkgdir}/usr/lib/initcpio/post/ergenos-secureboot"
  install -Dm644 95-ergenos-secureboot-grub.hook \
    "${pkgdir}/usr/share/libalpm/hooks/95-ergenos-secureboot-grub.hook"
  install -Dm644 10-ergenos-secureboot-remove-guard.hook \
    "${pkgdir}/usr/share/libalpm/hooks/10-ergenos-secureboot-remove-guard.hook"
  install -Dm755 removal-guard \
    "${pkgdir}/usr/lib/ergenos-secureboot/removal-guard"
}
