[CmdletBinding()]
param(
    [switch]$ApplyToBlankDrawing
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$TemplateTarget = 'D:\AI\CAD_Templates\AI_Drawing_Template.dwt'

$layerDefinitions = @(
    @{ Name = 'OUTLINE'; Color = 7; Linetype = 'Continuous' },
    @{ Name = 'CENTER'; Color = 1; Linetype = 'CENTER2' },
    @{ Name = 'HIDDEN'; Color = 2; Linetype = 'HIDDEN2' },
    @{ Name = 'HATCH'; Color = 3; Linetype = 'Continuous' },
    @{ Name = 'DIM'; Color = 6; Linetype = 'Continuous' },
    @{ Name = 'TEXT'; Color = 7; Linetype = 'Continuous' },
    @{ Name = 'AI_PREVIEW_OUTLINE'; Color = 7; Linetype = 'Continuous' },
    @{ Name = 'AI_PREVIEW_CENTER'; Color = 1; Linetype = 'CENTER2' },
    @{ Name = 'AI_PREVIEW_HIDDEN'; Color = 2; Linetype = 'HIDDEN2' },
    @{ Name = 'AI_PREVIEW_HATCH'; Color = 3; Linetype = 'Continuous' },
    @{ Name = 'AI_PREVIEW_DIM'; Color = 6; Linetype = 'Continuous' },
    @{ Name = 'AI_UNCERTAIN'; Color = 4; Linetype = 'HIDDEN2' }
)

if (-not $ApplyToBlankDrawing) {
    Write-Output 'DRY RUN: no AutoCAD document was changed.'
    Write-Output 'After explicit confirmation, rerun with -ApplyToBlankDrawing on an empty unsaved Drawing*.dwg.'
    Write-Output "The script will configure layers, AI_STANDARD text style, and AI_STANDARD_DIM."
    Write-Output "It will not save the DWT. Intended target: $TemplateTarget"
    exit 0
}

try {
    $acad = [Runtime.InteropServices.Marshal]::GetActiveObject('AutoCAD.Application')
}
catch {
    throw 'AutoCAD COM is not available.'
}
$doc = $acad.ActiveDocument
$documentPath = [string]$doc.Path
$documentName = [string]$doc.Name
if ($documentPath -or $documentName -notmatch '^Drawing\d*\.dwg$' -or [int]$doc.ModelSpace.Count -ne 0) {
    throw 'Refusing to modify the active document: use an empty, unsaved Drawing*.dwg.'
}

foreach ($linetype in @('CENTER2', 'HIDDEN2')) {
    try { $null = $doc.Linetypes.Item($linetype) }
    catch {
        try { $doc.Linetypes.Load($linetype, 'acadiso.lin') }
        catch { $doc.Linetypes.Load($linetype, 'acad.lin') }
    }
}

foreach ($definition in $layerDefinitions) {
    try { $layer = $doc.Layers.Item($definition.Name) }
    catch { $layer = $doc.Layers.Add($definition.Name) }
    $layer.Color = [int]$definition.Color
    $layer.Linetype = [string]$definition.Linetype
}

try { $textStyle = $doc.TextStyles.Item('AI_STANDARD') }
catch { $textStyle = $doc.TextStyles.Add('AI_STANDARD') }
try { $textStyle.SetFont('Arial', $false, $false, 0, 34) }
catch { $textStyle.FontFile = 'txt.shx' }

$doc.SetVariable('TEXTSTYLE', 'AI_STANDARD')
$doc.SetVariable('TEXTSIZE', 3.5)
$doc.SetVariable('DIMTXT', 3.5)
$doc.SetVariable('DIMASZ', 2.5)
$doc.SetVariable('DIMGAP', 1.0)
$doc.SetVariable('DIMDEC', 2)
$doc.SetVariable('DIMTAD', 1)
$doc.SetVariable('DIMTXSTY', 'AI_STANDARD')
$doc.SetVariable('LTSCALE', 1.0)

try { $dimStyle = $doc.DimStyles.Item('AI_STANDARD_DIM') }
catch { $dimStyle = $doc.DimStyles.Add('AI_STANDARD_DIM') }
$dimStyle.CopyFrom($doc)
$doc.ActiveDimStyle = $dimStyle
$doc.Regen(1)

Write-Output "Template settings applied to the unsaved blank drawing: $documentName"
Write-Output 'No entity was created and no file was saved.'
Write-Output "After visual review and separate user confirmation, save manually as: $TemplateTarget"
