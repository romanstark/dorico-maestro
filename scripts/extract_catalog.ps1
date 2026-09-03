# Regenerates docs/dorico_command_catalog.md from Dorico's shipped keycommands.json.
# Usage:  pwsh scripts/extract_catalog.ps1  [path-to-keycommands.json]  [out.md]
param(
    [string]$Path = "C:\Program Files\Steinberg\Dorico6\keycommands.json",
    [string]$Out  = (Join-Path $PSScriptRoot "..\docs\dorico_command_catalog.md")
)

$j = Get-Content $Path -Raw -Encoding UTF8 | ConvertFrom-Json
$cmds = New-Object System.Collections.Generic.List[string]
foreach ($grp in $j.PSObject.Properties) {
    $val = $grp.Value
    if ($val.contexts) {
        foreach ($ctx in $val.contexts) {
            foreach ($sc in $ctx.shortcuts) {
                foreach ($p in $sc.PSObject.Properties) { [void]$cmds.Add($p.Name) }
            }
        }
    }
}
$unique = $cmds | Sort-Object -Unique
$sb = New-Object System.Text.StringBuilder
[void]$sb.AppendLine("# Dorico Command Catalog (derived)")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("**Source:** derived from ``keycommands.json`` shipped with **Dorico 6** (Steinberg).")
[void]$sb.AppendLine("Only the command *identifiers* are listed (not key-binding assignments).")
[void]$sb.AppendLine("The Remote Control API executes these same command IDs.")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("**$($unique.Count) unique commands.**")
[void]$sb.AppendLine("")
$groups = $unique | Group-Object { ($_ -split '[.?]')[0] } | Sort-Object Name
foreach ($g in $groups) {
    [void]$sb.AppendLine("## $($g.Name)  ($($g.Count))")
    [void]$sb.AppendLine("")
    foreach ($c in ($g.Group | Sort-Object)) { [void]$sb.AppendLine("- ``$c``") }
    [void]$sb.AppendLine("")
}
$sb.ToString() | Out-File -FilePath $Out -Encoding UTF8
Write-Output "wrote $Out ($($unique.Count) commands)"
