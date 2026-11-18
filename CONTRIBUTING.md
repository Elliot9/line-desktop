# Contributing

Thanks for helping! Issues and pull requests are welcome. When reporting a problem, include your distribution and the output of `./line-app --version`.

## Layout

```
line-app      launcher: installs dependencies, builds the core, starts the UI
desktop/      GTK 4 interface (Python + PyGObject)
core/         communication core (Go): LINE protocol and encryption
scripts/      build, packaging, tests and README screenshots
docs/         architecture, status, guide and backlog
```

The interface and the core talk over a JSON-lines protocol; see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (in Traditional Chinese).

## Tests

```bash
cd core && go test ./... && cd ..                            # core tests
python3 scripts/check-core.py .build/line-core-linux-amd64   # core protocol check
python3 scripts/check-ui.py                                  # UI transport, chat state and updater (no display needed)
scripts/gui-smoke.sh                                         # GUI checks (needs xvfb xauth dbus-x11 imagemagick)
```

`./line-app --demo` opens offline sample data, handy for trying UI changes without an account.

## User-facing changes

Every change people will notice goes into the **未發布** (unreleased) section of [CHANGELOG.md](CHANGELOG.md), in plain Traditional Chinese for people using the app (新功能 / 改善 / 修正). That section becomes the release notes and is shown in the in-app update dialog, so keep it short. If a change affects the README, update both [README.md](README.md) and [README.zh-TW.md](README.zh-TW.md).

## Screenshots

The README screenshots come from the real app, driven by a stand-in core with made-up people and conversations:

```bash
scripts/screenshots/shoot.sh      # writes docs/images/*.webp
```

It needs `gtk4-broadwayd` (package `libgtk-4-bin`), `dbus-run-session`, `curl` and Pillow (`python3-pil`). The stock photos listed in [scripts/screenshots/photos.json](scripts/screenshots/photos.json) are downloaded from Unsplash on the first run (Unsplash License).

## Releasing

1. Turn the changelog's **未發布** heading into `## X.Y.Z · YYYY-MM-DD`. That section is the release notes; start a new 未發布 section when the next change lands.
2. Build and publish:

```bash
echo X.Y.Z > VERSION
scripts/package.sh amd64
scripts/package.sh arm64
awk '/^## X\.Y\.Z /{f=1; next} /^## /{f=0} f' CHANGELOG.md > release-notes.md
gh release create vX.Y.Z dist/line-desktop-linux-*.tar.gz* --title "LINE for Linux X.Y.Z" --notes-file release-notes.md
```

Installed copies offer the new version the next time LINE opens. For the in-app updater:

- The tag is `v` followed by the version in `VERSION` (for example `v1.1.0`).
- The archives keep the names `line-desktop-linux-amd64.tar.gz` and `line-desktop-linux-arm64.tar.gz`.
- Don't mark it as a pre-release: users aren't offered pre-releases, so you can use one to test first.

The update dialog shows the release notes up to the first heading containing 安裝 (install).
