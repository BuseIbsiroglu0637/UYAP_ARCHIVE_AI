param(
  [ValidateSet('amd64','arm64','win32')]
  [string]$Arch = 'amd64'
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version 2.0
$PackageRoot = Split-Path -Parent $PSScriptRoot
$ReleaseContractPath = Join-Path $PackageRoot 'config\release-contract.json'
$ReleaseChannelsPath = Join-Path $PackageRoot 'config\release-channels.json'
if (-not (Test-Path $ReleaseContractPath)) { throw 'release-contract.json bulunamadi.' }
if (-not (Test-Path $ReleaseChannelsPath)) { throw 'release-channels.json bulunamadi.' }
$ReleaseContract = Get-Content -LiteralPath $ReleaseContractPath -Raw -Encoding UTF8 | ConvertFrom-Json
$ReleaseChannels = Get-Content -LiteralPath $ReleaseChannelsPath -Raw -Encoding UTF8 | ConvertFrom-Json
$ReleaseId = [string]$ReleaseContract.release.releaseId
$ReleaseChannel = [string]$ReleaseContract.release.channel
$ReleaseEnvironment = [string]$ReleaseContract.release.environment
$ChannelProperty = $ReleaseChannels.channels.PSObject.Properties[$ReleaseChannel]
if ($null -eq $ChannelProperty) { throw ('Bilinmeyen release channel: ' + $ReleaseChannel) }
$ChannelPolicy = $ChannelProperty.Value
if ([string]$ChannelPolicy.environment -ne $ReleaseEnvironment) { throw 'Release channel/environment sozlesmesi uyusmuyor.' }
# S2-01: one release channel across the extension and the native host.
# Product switches cannot be taken from chrome.storage or installer user input.
$JsPolicyPath = Join-Path $PackageRoot 'shared\commercial-beta-policy.mjs'
$PyPolicyPath = Join-Path $PackageRoot 'native\commercial_beta_policy.py'
if (-not (Test-Path $JsPolicyPath) -or -not (Test-Path $PyPolicyPath)) { throw 'S2-01 release policy module missing; packaging blocked.' }
$JsPolicy = Get-Content -LiteralPath $JsPolicyPath -Raw -Encoding UTF8
$PyPolicy = Get-Content -LiteralPath $PyPolicyPath -Raw -Encoding UTF8
$JsChannelDeclaration = 'export const RELEASE_CHANNEL = "' + $ReleaseChannel + '";'
$PyChannelDeclaration = 'RELEASE_CHANNEL = "' + $ReleaseChannel + '"'
if (-not $JsPolicy.Contains($JsChannelDeclaration) -or -not $PyPolicy.Contains($PyChannelDeclaration)) {
  throw 'Release channel mismatch between release-contract.json and JS/Python policy modules.'
}

# A local-only commercial beta must not inject portal content scripts or retain
# portal host permissions. Current RC retains its historical behavior. Until a
# dedicated beta manifest is built and reviewed, distributing beta is blocked.
if ($ReleaseChannel -eq 'beta' -or $ReleaseChannel -eq 'stable') {
  $ExtensionManifestPath = Join-Path $PackageRoot 'manifest.json'
  $ExtensionManifest = Get-Content -LiteralPath $ExtensionManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
  $PortalHosts = @($ExtensionManifest.host_permissions | Where-Object { [string]$_ -match 'uyap\\.gov\\.tr' })
  $PortalContent = @($ExtensionManifest.content_scripts | Where-Object {
    (@($_.matches) -join ';') -match 'uyap\\.gov\\.tr'
  })
  if ($PortalHosts.Count -gt 0 -or $PortalContent.Count -gt 0) {
    throw 'Commercial local-only release cannot contain UYAP portal host permissions or content scripts.'
  }
}

$PackageDirName = 'UYAP_Archive_AI_' + $ReleaseId
Write-Host ('Release contract: ' + $ReleaseId + ' / ' + $ReleaseChannel + ' / ' + $ReleaseEnvironment)
if ($ChannelPolicy.signedBuildRequired -eq $true) { Write-Warning 'Bu kanal imzali build gerektirir. Phase 2 code-signing kapisi kapanmadan bu cikti production-ready sayilmaz.' }
$BuildRoot = Join-Path $env:TEMP ('UYAPArchiveAI-SetupBuild-' + [guid]::NewGuid().ToString('N'))
$Work = Join-Path $BuildRoot 'work'
$PayloadStage = Join-Path $BuildRoot 'payload'
New-Item -ItemType Directory -Force -Path $Work,$PayloadStage | Out-Null

$specs = @{
  amd64 = @{file='python-3.13.15-embed-amd64.zip';url='https://www.python.org/ftp/python/3.13.15/python-3.13.15-embed-amd64.zip';sha='d1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf'}
  arm64 = @{file='python-3.13.15-embed-arm64.zip';url='https://www.python.org/ftp/python/3.13.15/python-3.13.15-embed-arm64.zip';sha='cd992cbfb33be433ff20f150691595efb2862e56f4f1bec684c6077d4775af8e'}
  win32 = @{file='python-3.13.15-embed-win32.zip';url='https://www.python.org/ftp/python/3.13.15/python-3.13.15-embed-win32.zip';sha='3f5506367943d16dffa89f0ea140ebf04481a80611eb119e76e95aea8c79f5ce'}
}
$spec=$specs[$Arch]
$runtimeCache=Join-Path $PackageRoot 'native\runtime-cache'
New-Item -ItemType Directory -Force -Path $runtimeCache | Out-Null
$runtimeZip=Join-Path $runtimeCache $spec.file
if (-not (Test-Path $runtimeZip) -or (Get-FileHash $runtimeZip -Algorithm SHA256).Hash.ToLowerInvariant() -ne $spec.sha) {
  Write-Host 'Resmi Python embeddable runtime indiriliyor...'
  try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}
  Invoke-WebRequest -UseBasicParsing -Uri $spec.url -OutFile $runtimeZip
}
$hash=(Get-FileHash $runtimeZip -Algorithm SHA256).Hash.ToLowerInvariant()
if ($hash -ne $spec.sha) { throw 'Runtime SHA-256 dogrulamasi basarisiz.' }

$copyRoot=Join-Path $PayloadStage $PackageDirName
New-Item -ItemType Directory -Force -Path $copyRoot | Out-Null
Get-ChildItem -LiteralPath $PackageRoot -Force | Where-Object { $_.Name -notlike ($PackageDirName + '_Setup*.exe') } | ForEach-Object {
  Copy-Item -LiteralPath $_.FullName -Destination $copyRoot -Recurse -Force
}
$payloadZip=Join-Path $Work 'payload.zip'
Compress-Archive -Path (Join-Path $PayloadStage '*') -DestinationPath $payloadZip -CompressionLevel Optimal -Force

$bootstrap=Join-Path $Work 'bootstrap.cmd'
$bootstrapText=@"
@echo off
setlocal
chcp 65001 >nul
set "DEST=%LOCALAPPDATA%\UYAPArsiv\Distribution\$ReleaseId"
if exist "%DEST%" rmdir /s /q "%DEST%"
mkdir "%DEST%" >nul 2>&1
powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath '%~dp0payload.zip' -DestinationPath '%DEST%' -Force"
if errorlevel 1 exit /b 1
call "%DEST%\$PackageDirName\SETUP_UYAP_ARCHIVE_AI.bat"
set "EC=%ERRORLEVEL%"
if "%EC%"=="0" explorer "%DEST%\$PackageDirName"
exit /b %EC%
"@
$bootstrapText | Set-Content -LiteralPath $bootstrap -Encoding ASCII

$target=Join-Path $PackageRoot ($PackageDirName + "_Setup_" + $Arch + '.exe')
$sed=Join-Path $Work 'setup.sed'
$workEsc=$Work.Replace('\','\\')
$targetEsc=$target.Replace('\','\\')
$sedText=@"
[Version]
Class=IEXPRESS
SEDVersion=3
[Options]
PackagePurpose=InstallApp
ShowInstallProgramWindow=1
HideExtractAnimation=0
UseLongFileName=1
InsideCompressed=0
CAB_FixedSize=0
CAB_ResvCodeSigning=0
RebootMode=N
InstallPrompt=
DisplayLicense=
FinishMessage=
TargetName=$target
FriendlyName=UYAP Archive AI $ReleaseId Setup
AppLaunched=bootstrap.cmd
PostInstallCmd=<None>
AdminQuietInstCmd=
UserQuietInstCmd=
SourceFiles=SourceFiles
[SourceFiles]
SourceFiles0=$Work\
[SourceFiles0]
%FILE0%=
%FILE1%=
[Strings]
FILE0=payload.zip
FILE1=bootstrap.cmd
"@
$sedText | Set-Content -LiteralPath $sed -Encoding ASCII
$iexpress=Join-Path $env:SystemRoot 'System32\iexpress.exe'
if (-not (Test-Path $iexpress)) { throw 'IExpress bulunamadi. Bu Windows surumunde tek EXE olusturucu mevcut degil.' }
& $iexpress /N $sed
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $target)) { throw 'IExpress Setup.exe olusturamadi.' }
Write-Host ('Hazir offline kurulum EXE: ' + $target)
Remove-Item -LiteralPath $BuildRoot -Recurse -Force -ErrorAction SilentlyContinue
