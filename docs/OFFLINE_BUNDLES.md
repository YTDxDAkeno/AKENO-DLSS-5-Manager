# Personal offline Windows packages

Run from a Windows checkout with Python 3.11+, `pip install -r requirements-dev.txt`, and Inno Setup 6 installed:

```powershell
.\tools\build-private-offline.ps1 `
  -NeuralRuntime "C:\Users\justi\Downloads\nvngx_dlssnr.dll" `
  -BackendArchive "C:\Users\justi\Downloads\OptiScaler-DLSS5-1.3.0-win-x64.zip" `
  -OutputDirectory ".\dist-private" `
  -LocalUsePermitted
```

The backend archive must match the existing reviewed catalog pin. `-BackendArchive` is optional: without it the build downloads the pinned open-source backend once. The build also obtains pinned MIT-licensed PresentMon for offline capture. These are build-time downloads; the packaged experimental installation does not need network access. Add `-SuperResolutionRuntime "C:\path\nvngx_dlss.dll"` to bundle an independently NVIDIA-verified SR runtime for games that lack it. No upstream installer is run. The named Bandukids runtime retains its published source automatically; another local runtime also needs `-RuntimeSource "https://…/specific-source-page"`. Supplying permission is a local-use attestation, not a grant of redistribution rights or a security review.

The command builds `AKENO.exe`, copies and verifies supplied runtime bytes, writes `runtimes/manifest.json`, and creates a personal portable ZIP, Windows installer and SHA256SUMS.txt. Use a new output directory each time. Never upload the private output. The source repository ignores runtime binaries and private output; the private script refuses GitHub Actions execution.

The resulting OptiScaler package can install offline into eligible games with native DLSS SR already available. Games without SR require a separately verified NVIDIA SR dependency; Feeder has additional ReShade/consumer requirements and is not made complete by this OptiScaler package. The private build includes hash-verified PresentMon. Performance capture still requires Windows ETW permissions. Unavailable components are reported rather than fabricated or downloaded by the offline experimental path when they are present in the bundle.

In Secure Install choose **Install Experimental DLSS 5**, explicitly choose a bundled candidate, confirm local-use rights, offline single-player and experimental consent, then install. The preview checks all backend members and conflicts before the confirmation. Use **Test Compatibility** for preparation only; use **Restore experiment** to revert the recorded transaction. Imported candidates stay outside the official NVIDIA runtime store.

Public builds contain only an empty runtime inventory and the application. They do not claim to include your local DLL. There is no mandatory account or report upload.
