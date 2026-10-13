# AssetRipper-CLI Usage

## Tool Location

```
Tools/AssetRipper-CLI/artifacts/bin/AssetRipper.Tools.ExportRunner/Release/net10.0/AssetRipper.Tools.ExportRunner.exe
```

Requires .NET 10 SDK (`winget install Microsoft.DotNet.SDK.10`).

## Core Commands

| Command | Description |
|---------|-------------|
| `inspect <path>` | Scan game directory to understand resource content |
| `export <path> --output <output> --profile <profile>` | Export resources by category |
| `analyze <path> --report out.json` | Analyze and export JSON report |
| `report <manifest.json>` | View export manifest |

## Export Profiles (`--profile`)

| Profile | Description |
|---------|-------------|
| `cg` | Static CG images (event illustrations, gallery) |
| `audio` | Audio (BGM, voice, sound effects) |
| `characters` | Character sprites, portraits |
| `backgrounds` | Background/scene art |
| `sprites` | Sprite images |
| `ui` | UI elements |
| `player-art` | Player art assets |
| `narrative` | Narrative/story text assets |
| `full-raw` | Full raw extraction (no filter) |
| `full-project` | Complete Unity project export |

## Examples

```powershell
# Alias (PowerShell)
Set-Alias arc "Tools/AssetRipper-CLI/artifacts/bin/AssetRipper.Tools.ExportRunner/Release/net10.0/AssetRipper.Tools.ExportRunner.exe"

# Scan game
arc inspect ./Game

# Extract CG
arc export ./Game --output ./output/cg --profile cg

# Extract audio
arc export ./Game --output ./output/audio --profile audio

# Full extraction
arc export ./Game --output ./output/all --mode primary
```
