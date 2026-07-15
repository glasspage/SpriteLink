# Building SpriteLink for Windows

SpriteLink is bundled as a PyInstaller one-folder application and then wrapped
in a single Inno Setup installer. The installed application does not require a
separate Python installation.

## Local build

Install Python 3.12 and Inno Setup 6 on Windows, then run:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-build.txt
.\packaging\windows\build.ps1 -Version 0.1.0
```

The installer is written to:

```text
dist/installer/SpriteLink-Setup-0.1.0.exe
```

## GitHub Actions

The `Windows package` workflow can be used in two ways:

- Push a semantic version tag such as `v0.1.0` to build and publish a GitHub
  Release automatically.
- Run the workflow manually and leave **Publish a GitHub Release** disabled to
  produce a test artifact without publishing it. Enable that option to create
  the version tag and GitHub Release from the selected branch.

Release versions must contain exactly three numeric components, such as
`0.1.0`. Draft and prerelease channels can be added when integrated updating is
implemented.

