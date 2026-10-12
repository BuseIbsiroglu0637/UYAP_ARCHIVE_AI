param(
  [ValidateSet('Install','Repair','Diagnose','Rollback','Uninstall')]
  [string]$Mode = 'Install'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$HostName = 'tr.gov.uyap.archive_agent'
$AgentVersion = '2.7.67'
$PackageVersion = '2.9.0 RC15.9.38 ARCHIVE-SYNC-RACE-FIX (full kit)'
$CurrentExtensionId = 'fppnhbkemmbocmicplkfidcbggeininh'
$LegacyExtensionId = 'mkcemhoekaohggeebgkifcmjbilincfl'
$AllowedOrigins = @(
  "chrome-extension://$CurrentExtensionId/",
  "chrome-extension://$LegacyExtensionId/"
)

$PackageRoot = Split-Path -Parent $PSScriptRoot
$AppRoot = Join-Path $env:LOCALAPPDATA 'UYAPArsiv'
$AgentRoot = Join-Path $AppRoot 'Agent'
$CurrentDir = Join-Path $AgentRoot 'current'
$RollbackDir = Join-Path $AgentRoot 'rollback'
$ManifestPath = Join-Path $AgentRoot ($HostName + '.json')
$LogPath = Join-Path $AppRoot 'native-install.log'
$DiagnosticPath = Join-Path $AppRoot 'native-install-diagnostic.json'
$DownloadCache = Join-Path $AppRoot 'downloads'
$RuntimeCache = Join-Path $PSScriptRoot 'runtime-cache'
$ProtocolTest = Join-Path $PSScriptRoot 'portable_protocol_self_test.py'
$SiteCustomizeTemplate = Join-Path $PSScriptRoot 'portable_sitecustomize.py'
$AgentSource = Join-Path $PSScriptRoot 'uyap_archive_agent.py'
$AiIndexSource = Join-Path $PSScriptRoot 'ai_local_index.py'
$AiProviderSource = Join-Path $PSScriptRoot 'ai_provider.py'
$HostSelfTestSource = Join-Path $PSScriptRoot 'host_self_test.py'

New-Item -ItemType Directory -Force -Path $AppRoot | Out-Null
New-Item -ItemType Directory -Force -Path $AgentRoot | Out-Null
New-Item -ItemType Directory -Force -Path $DownloadCache | Out-Null

$script:Diagnostic = [ordered]@{
  schemaVersion = 2
  kind = 'UYAP_NATIVE_INSTALL_DIAGNOSTIC'
  packageVersion = $PackageVersion
  agentVersion = $AgentVersion
  mode = $Mode
  startedAt = (Get-Date).ToUniversalTime().ToString('o')
  finishedAt = $null
  ok = $false
  error = $null
  errorType = $null
  architecture = $null
  runtime = $null
  paths = [ordered]@{
    appRoot = $AppRoot
    agentRoot = $AgentRoot
    current = $CurrentDir
    rollback = $RollbackDir
    manifest = $ManifestPath
    log = $LogPath
  }
  registry = @()
  manifest = $null
  selfTest = $null
  browser = [ordered]@{}
  cleanup = [ordered]@{}
}

function Write-Utf8NoBom([string]$Path, [string]$Text) {
  $enc = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, $Text, $enc)
}

function Add-Log([string]$Message, [string]$Level = 'INFO') {
  $line = ('{0} [{1}] {2}' -f (Get-Date).ToString('yyyy-MM-dd HH:mm:ss.fff'), $Level, $Message)
  Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
  Write-Host $line
}

function Save-Diagnostic {
  try {
    $script:Diagnostic.finishedAt = (Get-Date).ToUniversalTime().ToString('o')
    $json = $script:Diagnostic | ConvertTo-Json -Depth 12
    Write-Utf8NoBom -Path $DiagnosticPath -Text $json
  } catch {
    try { Add-Content -LiteralPath $LogPath -Value ('DIAGNOSTIC_WRITE_FAILED: ' + $_.Exception.Message) -Encoding UTF8 } catch {}
  }
}

function Get-ArchitectureSpec {
  $raw = @($env:PROCESSOR_ARCHITECTURE, $env:PROCESSOR_ARCHITEW6432) -join '|'
  if ($raw -match 'ARM64') {
    return [ordered]@{
      arch = 'arm64'
      file = 'python-3.13.15-embed-arm64.zip'
      url = 'https://www.python.org/ftp/python/3.13.15/python-3.13.15-embed-arm64.zip'
      sha256 = 'cd992cbfb33be433ff20f150691595efb2862e56f4f1bec684c6077d4775af8e'
    }
  }
  if ([Environment]::Is64BitOperatingSystem) {
    return [ordered]@{
      arch = 'amd64'
      file = 'python-3.13.15-embed-amd64.zip'
      url = 'https://www.python.org/ftp/python/3.13.15/python-3.13.15-embed-amd64.zip'
      sha256 = 'd1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf'
    }
  }
  return [ordered]@{
    arch = 'win32'
    file = 'python-3.13.15-embed-win32.zip'
    url = 'https://www.python.org/ftp/python/3.13.15/python-3.13.15-embed-win32.zip'
    sha256 = '3f5506367943d16dffa89f0ea140ebf04481a80611eb119e76e95aea8c79f5ce'
  }
}

function Get-RegistryTargets {
  return @(
    "HKCU:\Software\Google\Chrome\NativeMessagingHosts\$HostName",
    "HKCU:\Software\Microsoft\Edge\NativeMessagingHosts\$HostName",
    "HKCU:\Software\Chromium\NativeMessagingHosts\$HostName"
  )
}

function Read-RegistryState {
  $rows = @()
  foreach ($key in Get-RegistryTargets) {
    $exists = Test-Path $key
    $value = $null
    if ($exists) {
      try { $value = (Get-Item -LiteralPath $key).GetValue('') } catch {}
    }
    $rows += [ordered]@{ key = $key; exists = $exists; value = $value }
  }
  return $rows
}

function Write-RegistryEntries {
  foreach ($key in Get-RegistryTargets) {
    New-Item -Path $key -Force | Out-Null
    Set-Item -LiteralPath $key -Value $ManifestPath
  }
  $script:Diagnostic.registry = Read-RegistryState
}

function Restore-RegistryState($State) {
  foreach ($row in @($State)) {
    $key = [string]$row.key
    if ($row.exists) {
      New-Item -Path $key -Force | Out-Null
      Set-Item -LiteralPath $key -Value ([string]$row.value)
    } else {
      if (Test-Path $key) { Remove-Item -LiteralPath $key -Recurse -Force }
    }
  }
}

function Remove-RegistryEntries {
  foreach ($key in Get-RegistryTargets) {
    if (Test-Path $key) { Remove-Item -LiteralPath $key -Recurse -Force }
  }
  $script:Diagnostic.registry = Read-RegistryState
}

function Get-HostExecutable([string]$Root) {
  $portable = Join-Path $Root 'runtime\python.exe'
  $legacy = Join-Path $Root 'uyap_archive_agent.exe'
  if (Test-Path $portable) { return $portable }
  if (Test-Path $legacy) { return $legacy }
  return $null
}

function Write-HostManifest {
  $exe = Get-HostExecutable -Root $CurrentDir
  if (-not $exe) { throw 'Aktif ajan çalıştırılabilir dosyası bulunamadı.' }
  $body = [ordered]@{
    name = $HostName
    description = 'UYAP Archive AI Native Agent'
    path = $exe
    type = 'stdio'
    allowed_origins = $AllowedOrigins
  }
  $json = $body | ConvertTo-Json -Depth 5
  Write-Utf8NoBom -Path $ManifestPath -Text $json
  $script:Diagnostic.manifest = $body
  return $exe
}

function Stop-UyapHostProcesses {
  Add-Log 'Eski UYAP ajan süreçleri kontrol ediliyor.'
  foreach ($name in @('uyap_archive_agent.exe','python.exe')) {
    try {
      $items = Get-CimInstance Win32_Process -Filter ("Name='" + $name + "'") -ErrorAction SilentlyContinue
      foreach ($item in @($items)) {
        $path = [string]$item.ExecutablePath
        $cmd = [string]$item.CommandLine
        $ours = $false
        if ($path -and $path.StartsWith($AppRoot, [System.StringComparison]::OrdinalIgnoreCase)) { $ours = $true }
        if (-not $ours -and $cmd -and $cmd.IndexOf($AppRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) { $ours = $true }
        if ($ours) {
          try { Stop-Process -Id ([int]$item.ProcessId) -Force -ErrorAction Stop; Add-Log ("Eski ajan süreci kapatıldı: PID " + $item.ProcessId) } catch {}
        }
      }
    } catch {}
  }
}

function Get-VerifiedRuntimeZip {
  $spec = Get-ArchitectureSpec
  $script:Diagnostic.architecture = $spec.arch
  $script:Diagnostic.runtime = $spec
  $candidates = @(
    (Join-Path $RuntimeCache $spec.file),
    (Join-Path $DownloadCache $spec.file)
  )
  foreach ($candidate in $candidates) {
    if (Test-Path $candidate) {
      try {
        $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $candidate).Hash.ToLowerInvariant()
        if ($hash -eq $spec.sha256) {
          Add-Log ("Doğrulanmış gömülü Python önbelleği kullanılıyor: " + $candidate)
          return $candidate
        }
        Add-Log ("Önbellek SHA-256 uyuşmuyor, kullanılmayacak: " + $candidate) 'WARN'
      } catch {}
    }
  }

  $target = Join-Path $DownloadCache $spec.file
  Add-Log ("Resmi Python.org gömülü çalışma zamanı indiriliyor: " + $spec.url)
  try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}
  Invoke-WebRequest -UseBasicParsing -Uri $spec.url -OutFile $target
  $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $target).Hash.ToLowerInvariant()
  if ($actual -ne $spec.sha256) {
    Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
    throw ("Python runtime SHA-256 doğrulaması başarısız. Beklenen=" + $spec.sha256 + " Gerçek=" + $actual)
  }
  Add-Log 'Python runtime SHA-256 doğrulaması geçti.'
  return $target
}

function Configure-EmbeddedRuntime([string]$RuntimeDir) {
  $pth = Get-ChildItem -LiteralPath $RuntimeDir -Filter 'python*._pth' -File | Select-Object -First 1
  if (-not $pth) { throw 'Gömülü Python _pth dosyası bulunamadı.' }
  $lines = [System.Collections.Generic.List[string]]::new()
  $hasSite = $false
  foreach ($line in [System.IO.File]::ReadAllLines($pth.FullName)) {
    if ($line.Trim() -eq '#import site' -or $line.Trim() -eq 'import site') {
      $lines.Add('import site')
      $hasSite = $true
    } else {
      $lines.Add($line)
    }
  }
  if (-not $hasSite) { $lines.Add('import site') }
  [System.IO.File]::WriteAllLines($pth.FullName, $lines.ToArray(), [System.Text.Encoding]::ASCII)
  Copy-Item -LiteralPath $AgentSource -Destination (Join-Path $RuntimeDir 'uyap_archive_agent.py') -Force
  Copy-Item -LiteralPath $AiIndexSource -Destination (Join-Path $RuntimeDir 'ai_local_index.py') -Force
  Copy-Item -LiteralPath $AiProviderSource -Destination (Join-Path $RuntimeDir 'ai_provider.py') -Force
  Copy-Item -LiteralPath $HostSelfTestSource -Destination (Join-Path $RuntimeDir 'host_self_test.py') -Force
  Copy-Item -LiteralPath $SiteCustomizeTemplate -Destination (Join-Path $RuntimeDir 'sitecustomize.py') -Force
}

function Invoke-PortableSelfTest([string]$PythonExe) {
  if (-not (Test-Path $ProtocolTest)) { throw 'Portable protocol test dosyası bulunamadı.' }
  $origin = $AllowedOrigins[0]
  Add-Log ("Native Messaging 2/2 ping testi: " + $PythonExe)
  $output = @(& $PythonExe -S $ProtocolTest $PythonExe $origin 2>&1)
  $exitCode = $LASTEXITCODE
  $joined = ($output | ForEach-Object { [string]$_ }) -join "`n"
  if ($exitCode -ne 0) { throw ("Portable Native Messaging testi başarısız (exit=" + $exitCode + "): " + $joined) }
  $last = @($output | ForEach-Object { [string]$_ } | Where-Object { $_.Trim() })[-1]
  try { $result = $last | ConvertFrom-Json } catch { throw ("Self-test JSON çözümlenemedi: " + $joined) }
  if (-not $result.ok -or [int]$result.pingCount -ne 2) { throw ("Self-test başarısız: " + $last) }
  if ([string]$result.version -ne $AgentVersion) { throw ("Ajan sürümü beklenenden farklı: " + $result.version + " / " + $AgentVersion) }
  $script:Diagnostic.selfTest = $result
  Add-Log ("BAGLANTI TESTI BASARILI - Ajan " + $result.version + " - 2/2 ping")
  return $result
}

function Prepare-Staging {
  $zip = Get-VerifiedRuntimeZip
  $stage = Join-Path $AgentRoot ('.staging-' + [guid]::NewGuid().ToString('N'))
  $runtime = Join-Path $stage 'runtime'
  New-Item -ItemType Directory -Force -Path $runtime | Out-Null
  Add-Log ("Runtime açılıyor: " + $zip)
  Expand-Archive -LiteralPath $zip -DestinationPath $runtime -Force
  Configure-EmbeddedRuntime -RuntimeDir $runtime
  Copy-Item -LiteralPath $ProtocolTest -Destination (Join-Path $stage 'portable_protocol_self_test.py') -Force
  Write-Utf8NoBom -Path (Join-Path $stage 'VERSION.txt') -Text ($AgentVersion + "`r`n" + $PackageVersion + "`r`n")
  $python = Join-Path $runtime 'python.exe'
  if (-not (Test-Path $python)) { throw 'Gömülü python.exe bulunamadı.' }
  Invoke-PortableSelfTest -PythonExe $python | Out-Null
  return $stage
}

function Cleanup-LegacyArtifacts {
  $removed = @()
  $versions = Join-Path $AppRoot 'versions'
  if (Test-Path $versions) {
    try { Remove-Item -LiteralPath $versions -Recurse -Force; $removed += $versions } catch { Add-Log ("Eski versions klasörü silinemedi: " + $_.Exception.Message) 'WARN' }
  }
  foreach ($dir in Get-ChildItem -LiteralPath $AgentRoot -Directory -ErrorAction SilentlyContinue) {
    if ($dir.Name -like '.staging-*' -or $dir.Name -like '.failed-*' -or $dir.Name -like '.swap-*') {
      try { Remove-Item -LiteralPath $dir.FullName -Recurse -Force; $removed += $dir.FullName } catch {}
    }
  }
  $script:Diagnostic.cleanup = [ordered]@{ removed = $removed; rollbackKept = (Test-Path $RollbackDir) }
}

function Install-PortableAgent {
  if (-not (Test-Path $AgentSource)) { throw 'uyap_archive_agent.py paket içinde bulunamadı.' }
  if (-not (Test-Path $AiIndexSource)) { throw 'ai_local_index.py paket içinde bulunamadı.' }
  if (-not (Test-Path $AiProviderSource)) { throw 'ai_provider.py paket içinde bulunamadı.' }
  if (-not (Test-Path $SiteCustomizeTemplate)) { throw 'portable_sitecustomize.py paket içinde bulunamadı.' }

  $registryBefore = Read-RegistryState
  $manifestBefore = $null
  if (Test-Path $ManifestPath) { try { $manifestBefore = [System.IO.File]::ReadAllText($ManifestPath) } catch {} }
  $hadCurrent = Test-Path $CurrentDir
  $stage = $null
  try {
    $stage = Prepare-Staging
    Stop-UyapHostProcesses
    if (Test-Path $RollbackDir) { Remove-Item -LiteralPath $RollbackDir -Recurse -Force }
    if (Test-Path $CurrentDir) { Move-Item -LiteralPath $CurrentDir -Destination $RollbackDir }
    Move-Item -LiteralPath $stage -Destination $CurrentDir
    $stage = $null
    $exe = Write-HostManifest
    Write-RegistryEntries
    Invoke-PortableSelfTest -PythonExe $exe | Out-Null
    Cleanup-LegacyArtifacts
    $script:Diagnostic.ok = $true
    Add-Log 'Portable Native Agent kurulumu başarıyla tamamlandı.'
  } catch {
    $message = $_.Exception.Message
    Add-Log ("Kurulum başarısız, rollback deneniyor: " + $message) 'ERROR'
    try { Stop-UyapHostProcesses } catch {}
    if ($stage -and (Test-Path $stage)) { try { Remove-Item -LiteralPath $stage -Recurse -Force } catch {} }
    try {
      if (Test-Path $CurrentDir) { Remove-Item -LiteralPath $CurrentDir -Recurse -Force }
      if (Test-Path $RollbackDir) { Move-Item -LiteralPath $RollbackDir -Destination $CurrentDir }
    } catch {}
    try {
      if ($null -ne $manifestBefore) { Write-Utf8NoBom -Path $ManifestPath -Text $manifestBefore }
      elseif (Test-Path $ManifestPath) { Remove-Item -LiteralPath $ManifestPath -Force }
    } catch {}
    try { Restore-RegistryState -State $registryBefore } catch {}
    throw
  }
}

function Diagnose-Agent {
  $script:Diagnostic.registry = Read-RegistryState
  $script:Diagnostic.browser.chrome = [ordered]@{
    localAppData = (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
    programFiles = (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe')
  }
  $script:Diagnostic.browser.edge = [ordered]@{
    programFilesX86 = (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe')
    programFiles = (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe')
  }
  if (Test-Path $ManifestPath) {
    try { $script:Diagnostic.manifest = (Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json) } catch { $script:Diagnostic.manifest = [ordered]@{ parseError = $_.Exception.Message } }
  }
  $exe = Get-HostExecutable -Root $CurrentDir
  if (-not $exe) { throw 'Aktif Native Agent bulunamadı.' }
  $script:Diagnostic.paths.hostExecutable = $exe
  try {
    $sig = Get-AuthenticodeSignature -FilePath $exe
    $script:Diagnostic.runtimeSignature = [ordered]@{ status = [string]$sig.Status; signer = [string]$sig.SignerCertificate.Subject }
  } catch {}
  if ($exe.ToLowerInvariant().EndsWith('python.exe')) { Invoke-PortableSelfTest -PythonExe $exe | Out-Null }
  else { $script:Diagnostic.selfTest = [ordered]@{ legacyHost = $true; executable = $exe } }
  $script:Diagnostic.ok = $true
  Add-Log 'Tanılama tamamlandı.'
}

function Rollback-Agent {
  if (-not (Test-Path $RollbackDir)) { throw 'Rollback için önceki ajan kopyası bulunamadı.' }
  Stop-UyapHostProcesses
  $swap = Join-Path $AgentRoot ('.swap-' + [guid]::NewGuid().ToString('N'))
  if (Test-Path $CurrentDir) { Move-Item -LiteralPath $CurrentDir -Destination $swap }
  Move-Item -LiteralPath $RollbackDir -Destination $CurrentDir
  if (Test-Path $swap) { Move-Item -LiteralPath $swap -Destination $RollbackDir }
  $exe = Write-HostManifest
  Write-RegistryEntries
  if ($exe.ToLowerInvariant().EndsWith('python.exe')) { Invoke-PortableSelfTest -PythonExe $exe | Out-Null }
  $script:Diagnostic.ok = $true
  Add-Log 'Rollback başarıyla tamamlandı.'
}

function Uninstall-Agent {
  Stop-UyapHostProcesses
  Remove-RegistryEntries
  foreach ($path in @($CurrentDir, $RollbackDir)) {
    if (Test-Path $path) { Remove-Item -LiteralPath $path -Recurse -Force }
  }
  if (Test-Path $ManifestPath) { Remove-Item -LiteralPath $ManifestPath -Force }
  foreach ($dir in Get-ChildItem -LiteralPath $AgentRoot -Directory -ErrorAction SilentlyContinue) {
    if ($dir.Name -like '.staging-*' -or $dir.Name -like '.failed-*' -or $dir.Name -like '.swap-*') {
      try { Remove-Item -LiteralPath $dir.FullName -Recurse -Force } catch {}
    }
  }
  $versions = Join-Path $AppRoot 'versions'
  if (Test-Path $versions) { try { Remove-Item -LiteralPath $versions -Recurse -Force } catch {} }
  $script:Diagnostic.ok = $true
  Add-Log 'Native Agent kaldırıldı. UYAP arşiv evraklarına ve kullanıcı verilerine dokunulmadı.'
}

Add-Log ("=== UYAP Archive AI Native Installer başladı: " + $Mode + " ===")
try {
  switch ($Mode) {
    'Install' { Install-PortableAgent }
    'Repair' { Install-PortableAgent }
    'Diagnose' { Diagnose-Agent }
    'Rollback' { Rollback-Agent }
    'Uninstall' { Uninstall-Agent }
  }
} catch {
  $script:Diagnostic.ok = $false
  $script:Diagnostic.error = $_.Exception.Message
  $script:Diagnostic.errorType = $_.Exception.GetType().FullName
  Add-Log ("HATA: " + $_.Exception.Message) 'ERROR'
  Save-Diagnostic
  Write-Host ''
  Write-Host 'KURULUM / ISLEM BASARISIZ' -ForegroundColor Red
  Write-Host ("Tanılama: " + $DiagnosticPath)
  Write-Host ("Log: " + $LogPath)
  exit 1
} finally {
  Save-Diagnostic
}

Write-Host ''
if ($Mode -eq 'Install' -or $Mode -eq 'Repair') {
  Write-Host 'KURULUM BASARILI' -ForegroundColor Green
  Write-Host ("Native Agent: " + $AgentVersion + " / 2/2 ping")
  Write-Host 'Chrome/Edge tamamen kapatılıp yeniden açılmalıdır.'
} elseif ($Mode -eq 'Diagnose') {
  Write-Host 'TANILAMA BASARILI' -ForegroundColor Green
} elseif ($Mode -eq 'Rollback') {
  Write-Host 'GERI ALMA BASARILI' -ForegroundColor Green
} elseif ($Mode -eq 'Uninstall') {
  Write-Host 'AJAN KALDIRILDI' -ForegroundColor Green
}
Write-Host ("Tanılama: " + $DiagnosticPath)
Write-Host ("Log: " + $LogPath)
exit 0
