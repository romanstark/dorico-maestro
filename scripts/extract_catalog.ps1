# Regenerates docs/dorico_command_catalog.md from Dorico's shipped keycommands.json.
# Usage:  pwsh scripts/extract_catalog.ps1  [path-to-keycommands.json]  [out.md]
param(
    [string]$Path = "",
    [string]$Out  = (Join-Path $PSScriptRoot "..\docs\dorico_command_catalog.md")
)

if (-not $Path) {
    $Path = if ($IsMacOS) {
        "/Applications/Dorico 6.app/Contents/Resources/keycommands.json"
    } else {
        "C:\Program Files\Steinberg\Dorico6\keycommands.json"
    }
}

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
[void]$sb.AppendLine("# Dorico Command Catalog (Derived)")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("**Source:** Derived from `keycommands.json` shipped with **Dorico 6** (Steinberg).")
[void]$sb.AppendLine("This document lists command identifiers parsed from key-binding configuration files. The Dorico Remote Control API dispatches these identifiers.")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("**$($unique.Count) unique commands.** Regenerate using `scripts/extract_catalog.ps1`.")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("This listing represents key-bindable commands. Dorico accepts additional remote commands that are not exposed in key commands (for example `File.Export` and `Print.ExportCurrentLayoutAsPDF`).")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("Integration and verification statuses are maintained in `src/dorico_maestro/commands.yaml` (`verified`, `reachable`, `unavailable`, `broken`, `untested`).")
[void]$sb.AppendLine("Parameter bindings and typed schemas are defined in `src/dorico_maestro/commands.yaml`.")
[void]$sb.AppendLine("")
$groups = $unique | Group-Object { ($_ -split '[.?]')[0] } | Sort-Object Name
foreach ($g in $groups) {
    [void]$sb.AppendLine("## $($g.Name)  ($($g.Count))")
    [void]$sb.AppendLine("")
    foreach ($c in ($g.Group | Sort-Object)) { [void]$sb.AppendLine("- ``$c``") }
    [void]$sb.AppendLine("")
}
$resolvedOut = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Out)
[System.IO.File]::WriteAllText($resolvedOut, $sb.ToString())
Write-Output "wrote $Out ($($unique.Count) commands)"
