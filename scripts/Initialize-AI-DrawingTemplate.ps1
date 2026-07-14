[CmdletBinding()]
param(
    [switch]$ApplyToBlankDrawing,
    [switch]$SaveTemplate
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

if ($SaveTemplate -and -not $ApplyToBlankDrawing) {
    throw '-SaveTemplate requires -ApplyToBlankDrawing.'
}

if (-not $ApplyToBlankDrawing) {
    Write-Output 'DRY RUN: no AutoCAD document was changed.'
    Write-Output 'After explicit confirmation, rerun with -ApplyToBlankDrawing on an empty unsaved Drawing*.dwg.'
    Write-Output "The script will configure layers, AI_STANDARD text style, and AI_STANDARD_DIM."
    Write-Output "Add -SaveTemplate to save the reviewed blank document as: $TemplateTarget"
    exit 0
}

$acad = $null
foreach ($progId in @('AutoCAD.Application', 'AutoCAD.Application.24.1')) {
    try {
        $acad = [Runtime.InteropServices.Marshal]::GetActiveObject($progId)
        break
    }
    catch { }
}
if ($null -eq $acad) {
    throw 'AutoCAD COM is not available.'
}
$doc = $acad.ActiveDocument
$documentPath = [string]$doc.Path
$documentName = [string]$doc.Name
$documentFullName = [string]$doc.FullName
$modelSpaceCount = [int]$doc.ModelSpace.Count
$isGenericName = $documentName -match '^Drawing\d*\.dwg$'
$hasSavedFullPath = $documentFullName -and $documentFullName -ne $documentName
if (-not $isGenericName -or $hasSavedFullPath -or $modelSpaceCount -ne 0) {
    $identity = "Name=$documentName; Path=$documentPath; FullName=$documentFullName; ModelSpace.Count=$modelSpaceCount"
    throw "Refusing to modify the active document: use an empty, unsaved Drawing*.dwg. $identity"
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
try { $textStyle.SetFont('Microsoft YaHei', $false, $false, 0, 134) }
catch {
    try { $textStyle.SetFont('Arial', $false, $false, 0, 34) }
    catch { $textStyle.FontFile = 'txt.shx' }
}

$doc.SetVariable('INSUNITS', 4)
$doc.SetVariable('MEASUREMENT', 1)
$doc.SetVariable('LUNITS', 2)
$doc.SetVariable('LUPREC', 2)
$doc.SetVariable('TEXTSTYLE', 'AI_STANDARD')
$doc.SetVariable('TEXTSIZE', 3.5)
$doc.SetVariable('DIMTXT', 3.5)
$doc.SetVariable('DIMASZ', 2.5)
$doc.SetVariable('DIMGAP', 1.0)
$doc.SetVariable('DIMDEC', 2)
$doc.SetVariable('DIMTAD', 1)
$doc.SetVariable('DIMTXSTY', 'AI_STANDARD')
$doc.SetVariable('LTSCALE', 1.0)
$doc.SetVariable('MSLTSCALE', 1)
$doc.SetVariable('PSLTSCALE', 1)

try { $dimStyle = $doc.DimStyles.Item('AI_STANDARD_DIM') }
catch { $dimStyle = $doc.DimStyles.Add('AI_STANDARD_DIM') }
$dimStyle.CopyFrom($doc)
$doc.ActiveDimStyle = $dimStyle
$doc.Regen(1)

Write-Output "Template settings applied to the unsaved blank drawing: $documentName"
Write-Output 'No drawing entity was created.'

if ($SaveTemplate) {
    if (Test-Path -LiteralPath $TemplateTarget) {
        throw "Refusing to overwrite existing template: $TemplateTarget"
    }
    $targetDirectory = Split-Path -Parent $TemplateTarget
    $null = New-Item -ItemType Directory -Path $targetDirectory -Force
    $doc.SaveAs($TemplateTarget, 66) # ac2018_Template
    if (-not (Test-Path -LiteralPath $TemplateTarget)) {
        throw "AutoCAD did not create the expected template: $TemplateTarget"
    }
    Write-Output "Template saved and verified: $TemplateTarget"
}
else {
    Write-Output 'No file was saved.'
    Write-Output "After visual review, rerun with -ApplyToBlankDrawing -SaveTemplate."
}
