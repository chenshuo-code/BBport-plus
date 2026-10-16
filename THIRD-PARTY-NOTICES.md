# Third-party notices

bbport-plus is licensed under GPL-2.0-or-later (see `LICENSE`). It is a modified version of
[Supermedo/bloodborne_pc](https://github.com/Supermedo/bloodborne_pc), which builds on
[deadinside28/bloodborne_pc](https://github.com/deadinside28/bloodborne_pc) and the shadPS4 video core. The
notices of those projects and of their components are in `README.md` (upstream part, *Credits and licenses*),
`gpu/VENDOR.txt` and the licence files of each component folder.

## What the release files contain

The **overlay** (`bbport-plus-<version>-overlay.zip`) holds only the bbport-plus launcher:

- `Bloodborne.exe` and `_internal\`: the launcher (`launcher/bbport_launcher_win.py`, GPL-2.0-or-later) frozen
  with [PyInstaller](https://pyinstaller.org) (GPL-2.0-or-later with the bootloader exception). `_internal\`
  carries the runtime it needs:

| Component | Licence |
|---|---|
| CPython 3.14 (`python314.dll`, `*.pyd`, `base_library.zip`) | [PSF License](https://docs.python.org/3/license.html) |
| Tcl/Tk 9.0 (`tcl90.dll`, `tcl9tk90.dll`, `_tkinter.pyd`) | [Tcl/Tk (BSD-style)](https://www.tcl-lang.org/software/tcltk/license.html) |
| Pillow (`PIL\`) | [MIT-CMU / HPND](https://github.com/python-pillow/Pillow/blob/main/LICENSE) |
| OpenSSL 3 (`libcrypto-3.dll`, `libssl-3.dll`) | [Apache-2.0](https://www.openssl.org/source/license.html) |
| zlib, libffi, libtommath, expat (`pyexpat.pyd`), bzip2, liblzma, zstd | zlib, MIT, Public domain / BSD-style licences |
| Microsoft Visual C++ runtime (`VCRUNTIME140*.dll`) | Redistributable under the Microsoft Visual C++ Redistributable terms |

The **upstream package** (the native part: `bin\`, `fsr4_shaders\`, `licenses\`, ...) is **not** redistributed by
bbport-plus. `Install-BBportPlus.ps1` downloads it from the upstream release page and checks its SHA-256. It
contains, among others, NVIDIA's `nvngx_dlss.dll`, which is governed by NVIDIA's DLSS SDK licence
(`licenses\NVIDIA-DLSS-LICENSE.txt` inside the package). That licence does not allow distributing the DLL as a
stand-alone product or as part of software that must be redistributable at no charge, so this project leaves its
distribution to the upstream package.

## Mods

bbport-plus loads mods but includes none. They change the game's own files and have their own authors and terms.

## Source code

The source for every binary in the overlay is this repository at the release tag. The source for the upstream
package is [Supermedo/bloodborne_pc](https://github.com/Supermedo/bloodborne_pc) at tag `windows-v1.6`
(commit `e076119`), which this repository contains in its first import commit.

## Trademarks

Bloodborne is a trademark of Sony Interactive Entertainment Inc.; FromSoftware, NVIDIA, AMD and other names are
trademarks of their owners. bbport-plus is not affiliated with or endorsed by any of them and contains no game
files.
