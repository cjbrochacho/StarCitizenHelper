<#
.SYNOPSIS
  Render a key-binding reference PDF to the PNGs the Key Bindings tab shows.

.DESCRIPTION
  One page per PNG, named from -Names in page order. Uses the PDF renderer
  built into Windows (Windows.Data.Pdf, the one the Edge and Photos apps
  use), so nothing has to be installed - but that API is only reachable
  from Windows PowerShell 5.1, not PowerShell 7.

  Run it again when a new sheet comes out for a new patch, then update
  SOURCE.md beside it.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File render_sheet.ps1 `
      -Pdf "$env:USERPROFILE\Downloads\SC4_6_0_KeyboardMouseOnly_v1.pdf"

  Writes flight.png and fps.png next to this script, 2000 px wide.
#>
param(
    [Parameter(Mandatory = $true)] [string] $Pdf,
    [string]   $OutDir = '',
    [int]      $Width  = 2000,
    [string[]] $Names  = @('flight', 'fps')
)

$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -ge 6) {
    throw "Windows.Data.Pdf is not available in PowerShell $($PSVersionTable.PSVersion); run this with Windows PowerShell 5.1 (powershell.exe)."
}
# Default beside this script - resolved here, since $PSScriptRoot is not
# yet set while parameters are being bound.
if (-not $OutDir) { $OutDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
$Pdf = (Resolve-Path -LiteralPath $Pdf).Path
$OutDir = (Resolve-Path -LiteralPath $OutDir).Path

Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Data.Pdf.PdfDocument, Windows.Data.Pdf, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.RandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]

# WinRT async calls come back as IAsyncOperation<T> / IAsyncAction; .NET's
# AsTask extension turns them into something Wait() understands. The generic
# one is picked by reflection because PowerShell cannot name a generic
# extension method directly.
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 }
$asTaskOperation = ($asTask | Where-Object { $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
$asTaskAction    = ($asTask | Where-Object { $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' })[0]

function Await-Operation($operation, $resultType) {
    $task = $asTaskOperation.MakeGenericMethod($resultType).Invoke($null, @($operation))
    $task.Wait()
    $task.Result
}
function Await-Action($action) {
    $task = $asTaskAction.Invoke($null, @($action))
    $task.Wait()
}

$file = Await-Operation ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Pdf)) ([Windows.Storage.StorageFile])
$doc  = Await-Operation ([Windows.Data.Pdf.PdfDocument]::LoadFromFileAsync($file)) ([Windows.Data.Pdf.PdfDocument])
if ($doc.PageCount -gt $Names.Count) {
    Write-Warning ("The PDF has {0} pages but only {1} names were given; the rest are skipped." -f $doc.PageCount, $Names.Count)
}
$folder = Await-Operation ([Windows.Storage.StorageFolder]::GetFolderFromPathAsync($OutDir)) ([Windows.Storage.StorageFolder])

# The renderer scales DestinationWidth by the display's DPI setting, so a
# request for 2000 comes back 2500 on a 125% screen. Ask once, measure what
# came back, and correct the request for every page - the output is then the
# same width on any machine.
function Render-Page($page, $requestWidth, $name) {
    $options = New-Object Windows.Data.Pdf.PdfPageRenderOptions
    $options.DestinationWidth = $requestWidth
    $target = Await-Operation ($folder.CreateFileAsync($name, [Windows.Storage.CreationCollisionOption]::ReplaceExisting)) ([Windows.Storage.StorageFile])
    $stream = Await-Operation ($target.OpenAsync([Windows.Storage.FileAccessMode]::ReadWrite)) ([Windows.Storage.Streams.IRandomAccessStream])
    Await-Action ($page.RenderToStreamAsync($stream, $options))
    $stream.Dispose()
    # PNG width is a big-endian int at byte 16 of the IHDR chunk.
    $head = New-Object byte[] 24
    $fs = [System.IO.File]::OpenRead((Join-Path $OutDir $name))
    try { $null = $fs.Read($head, 0, 24) } finally { $fs.Dispose() }
    [System.Net.IPAddress]::NetworkToHostOrder([BitConverter]::ToInt32($head, 16))
}

$pages = [Math]::Min($doc.PageCount, $Names.Count)
$request = $Width
for ($i = 0; $i -lt $pages; $i++) {
    $page = $doc.GetPage($i)
    $name = $Names[$i] + '.png'
    $got = Render-Page $page $request $name
    if ($got -ne $Width) {
        $request = [int][Math]::Round($Width * $Width / $got)
        $got = Render-Page $page $request $name
    }
    $page.Dispose()
    $size = (Get-Item -LiteralPath (Join-Path $OutDir $name)).Length
    Write-Output ("page {0} -> {1}  {2} px wide  ({3:N0} bytes)" -f ($i + 1), $name, $got, $size)
}
