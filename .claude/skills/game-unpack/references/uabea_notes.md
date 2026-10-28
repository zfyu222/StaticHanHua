# UABEA CLI Usage Notes

## Tool Location

```
Tools/UABEA/UABEAvalonia/bin/Release/net8.0/UABEAvalonia.exe
```

Requires .NET 8.0 SDK to build.

## CLI Commands

| Command | Description | Usage |
|---------|-------------|-------|
| `batchexportbundle` | Batch export Bundle resources | `<dir> [-keepnames] [-kd] [-fd] [-md]` |
| `batchimportbundle` | Batch import modified resources | `<dir> [-keepnames] [-kd] [-fd] [-md]` |
| `applyemip` | Apply .emip package | `<emipFile> <dir>` |
| `list` | List all assets | `<assetsFile> [classId]` |
| `export` | Export single asset (raw binary) | `<assetsFile> <pathId> <outputFile>` |
| `import` | Import single asset (raw binary) | `<assetsFile> <pathId> <inputFile>` |
| `exporttext` | Export TextAsset text | `<assetsFile> <pathId> <outputFile>` |
| `importtext` | Import text to TextAsset | `<assetsFile> <pathId> <inputFile>` |
| `exportdump` | Export as UABE text format | `<assetsFile> <pathId> <outputFile>` |
| `importdump` | Import from UABE text format | `<assetsFile> <pathId> <inputFile>` |
| `exportjson` | Export as JSON | `<assetsFile> <pathId> <outputFile>` |
| `importjson` | Import from JSON | `<assetsFile> <pathId> <inputFile>` |

## Fixed CLI Issues (2026-07, based on Expeditions Viking)

| Issue | Symptom | Fix Location | Resolution |
|-------|---------|--------------|------------|
| Console crash | All CLI commands exit with no output | `Program.cs` line 30 | try-catch `IOException` on `Console.GetCursorPosition()`, skip in non-interactive console |
| Missing type database | `exporttext`/`importtext` NullReferenceException, GUI works but CLI crashes | `CommandLineHandler.cs` new `LoadClassPackage()` method | Call `am.LoadClassPackage("classdata.tpk")` + `am.LoadClassDatabaseFromPackage(unityVersion)` after `LoadAssetsFile` |
| File handle not released | `importtext` consecutive calls: "file being used by another process" | `CommandLineHandler.cs` importtext method | Call `am.UnloadAllAssetsFiles()` before `File.Delete` |
| importtext creates backup | Each call moves original to `.bak0000`, cannot batch | `CommandLineHandler.cs` importtext method | Removed backup logic, use `File.Delete` + `File.Move` to overwrite directly |

## Unfixed Issues (be aware)

- `exportdump`/`importdump`/`exportjson`/`importjson`: No `LoadClassPackage` call — may crash on Unity 5.x games without TypeTree
- `import`/`importdump`/`importjson`: No file handle release fix — consecutive calls may report file in use
- `list` command Name column: Empty for Unity 5.x without TypeTree (`GetAssetName` swallows exceptions). Use `exporttext` to match names by content
- `classdata.tpk`: Must be in same directory as `UABEAvalonia.exe` (`net8.0/`). Verify after rebuild
- Paths with spaces: Wrap in double quotes with `cmd /c`; `\u` may be parsed as Unicode escape — use paths without spaces (e.g., `C:\Temp\`)

## Debugging Tip

When CLI and GUI behavior diverge, compare GUI entry (`MainWindow.axaml.cs`) vs CLI entry (`CommandLineHandler.cs`) initialization code first, rather than debugging downstream functions.
