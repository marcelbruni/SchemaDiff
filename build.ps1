# Builds release\EplanSchemaVergleich.exe from app\eplan_diff_app.py and eplan_diff.py.
# Run again after every change to eplan_diff.py - the exe carries its own copy of the script.

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

python -m pip install --user --quiet -r "$root\requirements.txt"
python -m PyInstaller --noconfirm --onefile --noconsole `
    --name EplanSchemaVergleich `
    --paths $root `
    --collect-all tkinterdnd2 `
    --distpath "$root\release" `
    --workpath "$root\build\work" `
    --specpath "$root\build" `
    "$root\app\eplan_diff_app.py"

Get-Item "$root\release\EplanSchemaVergleich.exe" | Select-Object Name, Length, LastWriteTime
