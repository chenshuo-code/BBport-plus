# Contributing

Thanks for helping. bbport-plus is a small fork of [Supermedo/bloodborne_pc](https://github.com/Supermedo/bloodborne_pc);
bugs in the game runtime itself (the native part, `src/`, `gpu/`) are best reported upstream too.

## Rules that matter

- **No game material, ever.** No dumps, `eboot.bin`, `dvdroot_ps4` files, saves, or anything under an `out\` folder
  (those are derived from the game executable). Mods that other people made are not hosted here either; link to them.
- Keep the licence: everything is GPL-2.0-or-later, new files get the `SPDX-License-Identifier: GPL-2.0-or-later`
  header, and a changed upstream file gets a note in its header saying so and when.
- Do not add redistributable copies of proprietary binaries (for example NVIDIA's DLSS runtime).

## Where the changes live

Keep our changes small and separate from upstream files, so a new upstream version merges easily:

| What | Where |
|---|---|
| Run logs, crash reports, drop records | `launcher/bbport_diag.py` |
| Hooks into the launcher | `launcher/bbport_launcher_win.py` (marked `fork` / `bbport-plus`) |
| Release tooling | `packaging/windows/build_launcher.ps1`, `make_release.ps1`, `tools/windows/Install-BBportPlus.ps1.in` |
| Docs | `docs/DIAGNOSTICS.md`, `docs/releases/`, `CHANGELOG.md` |

To take a newer upstream version: `git fetch upstream windows && git merge upstream/windows`, then check
`launcher/bbport_launcher_win.py` for conflicts.

## Making a change

1. Create a branch, make the change, and try it with a real run if it touches the launcher
   (`packaging\windows\build_launcher.ps1 -Package <an upstream package>`).
2. Add a line under **Unreleased** in `CHANGELOG.md` (Added / Changed / Fixed).
3. Open a pull request and say what you ran it on (Windows version, graphics card).

## Releasing (maintainer)

1. Move the *Unreleased* entries to a new version section with the date; set `PLUS_VERSION` in
   `launcher/bbport_diag.py`; write `docs/releases/v<version>.md`. Commit.
2. Run `packaging\windows\make_release.ps1` from the clean tree and try `dist\release\Install-BBportPlus.ps1` into an
   empty NTFS folder.
3. `git tag -a v<version> -m "bbport-plus <version>"`, push the tag, create the GitHub release from the tag with the
   files in `dist\release\` and `RELEASE_NOTES.md` as the text.
