function Assert-Condition {
  param([bool] $Condition, [string] $Message)
  if (-not $Condition) {
    throw $Message
  }
}

function Invoke-BoundedProcess {
  param(
    [Parameter(Mandatory)] [string] $Label,
    [Parameter(Mandatory)] [string] $FilePath,
    [string] $Arguments = '',
    [Parameter(Mandatory)] [string] $WorkingDirectory,
    [Parameter(Mandatory)] [int] $TimeoutSeconds,
    [int[]] $AllowedExitCodes = @(0)
  )

  $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
  $startInfo.FileName = $FilePath
  $startInfo.Arguments = $Arguments
  $startInfo.WorkingDirectory = $WorkingDirectory
  $startInfo.UseShellExecute = $false
  $startInfo.CreateNoWindow = $true
  $startInfo.RedirectStandardOutput = $true
  $startInfo.RedirectStandardError = $true

  $process = [System.Diagnostics.Process]::new()
  $process.StartInfo = $startInfo
  try {
    Assert-Condition ($process.Start()) "$Label failed to start."
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
      try {
        $process.Kill($true)
      } catch {
        Write-Warning "$Label process-tree kill failed: $($_.Exception.Message)"
      }
      [void] $process.WaitForExit(10000)
      throw "$Label timed out after $TimeoutSeconds seconds."
    }
    $process.WaitForExit()
    $stdout = $stdoutTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()
    if ($AllowedExitCodes -notcontains $process.ExitCode) {
      throw "$Label exited $($process.ExitCode).`nstdout:`n$stdout`nstderr:`n$stderr"
    }
    return [pscustomobject]@{
      ExitCode = $process.ExitCode
      StdOut = $stdout
      StdErr = $stderr
    }
  } finally {
    $process.Dispose()
  }
}

function Invoke-InstalledAppSmoke {
  param(
    [Parameter(Mandatory)] [string] $FilePath,
    [Parameter(Mandatory)] [string] $WorkingDirectory,
    [Parameter(Mandatory)] [string] $SmokeHome,
    [Parameter(Mandatory)] [string] $ResultPath,
    [Parameter(Mandatory)] [string] $Version
  )

  $savedEnvironment = @{}
  foreach ($item in [Environment]::GetEnvironmentVariables('Process').GetEnumerator()) {
    $savedEnvironment[[string] $item.Key] = [string] $item.Value
  }
  $process = $null
  try {
    foreach ($name in @([Environment]::GetEnvironmentVariables('Process').Keys)) {
      if (
        $name -like 'PYTHON*' -or
        $name -ieq 'VIRTUAL_ENV' -or
        $name -like 'QT_*' -or
        $name -ieq 'VULKAN_SDK' -or
        $name -like 'ANKI_MINER_*' -or
        $name -in @('REQUESTS_CA_BUNDLE', 'SSL_CERT_FILE', 'CURL_CA_BUNDLE')
      ) {
        [Environment]::SetEnvironmentVariable([string] $name, $null, 'Process')
      }
    }
    [Environment]::SetEnvironmentVariable('PATH', "$env:SystemRoot\System32;$env:SystemRoot", 'Process')
    [Environment]::SetEnvironmentVariable('ANKI_MINER_HOME', $SmokeHome, 'Process')
    [Environment]::SetEnvironmentVariable('ANKI_MINER_SMOKE', 'installer', 'Process')
    [Environment]::SetEnvironmentVariable('ANKI_MINER_SMOKE_RESULT', $ResultPath, 'Process')
    [Environment]::SetEnvironmentVariable('ANKI_MINER_SMOKE_EXPECTED_VERSION', $Version, 'Process')

    $process = Start-Process -FilePath $FilePath -WorkingDirectory $WorkingDirectory -PassThru
    Assert-Condition ($null -ne $process) 'Installed app failed to start.'
    if (-not $process.WaitForExit(120000)) {
      try {
        $process.Kill($true)
      } catch {
        Write-Warning "Installed app process-tree kill failed: $($_.Exception.Message)"
      }
      [void] $process.WaitForExit(10000)
      throw 'Installed app timed out after 120 seconds.'
    }
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) {
      throw "Installed app exited $($process.ExitCode)."
    }
  } finally {
    if ($null -ne $process) {
      $process.Dispose()
    }
    foreach ($name in @([Environment]::GetEnvironmentVariables('Process').Keys)) {
      if (-not $savedEnvironment.ContainsKey([string] $name)) {
        [Environment]::SetEnvironmentVariable([string] $name, $null, 'Process')
      }
    }
    foreach ($item in $savedEnvironment.GetEnumerator()) {
      [Environment]::SetEnvironmentVariable([string] $item.Key, [string] $item.Value, 'Process')
    }
  }
}

function New-InstallerSmokeContext {
  param(
    [Parameter(Mandatory)] [string] $RepoRoot,
    [Parameter(Mandatory)] [string] $SmokeHome,
    [Parameter(Mandatory)] [string] $ResultFileName
  )

  $installDir = Join-Path $env:LOCALAPPDATA 'Programs\AnkiMiner'
  $desktopDir = [Environment]::GetFolderPath('Desktop')
  Assert-Condition (-not [string]::IsNullOrWhiteSpace($desktopDir)) 'Windows Desktop folder did not resolve.'
  $programsDir = [Environment]::GetFolderPath([Environment+SpecialFolder]::Programs)
  Assert-Condition (-not [string]::IsNullOrWhiteSpace($programsDir)) 'Windows Programs folder did not resolve.'
  $uninstallRoot = 'Registry::HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Uninstall'
  $uninstallKeyName = '{15B09250-AC39-4792-A15A-B73BD8E218A1}_is1'

  return [pscustomobject]@{
    RepoRoot = [System.IO.Path]::GetFullPath($RepoRoot)
    InstallDir = $installDir
    InstalledExe = Join-Path $installDir 'AnkiMiner.exe'
    DesktopShortcut = Join-Path $desktopDir 'Anki Miner.lnk'
    LegacyShortcut = Join-Path $env:USERPROFILE 'Anki Miner.lnk'
    StartMenuGroup = Join-Path $programsDir 'Anki Miner'
    UninstallRoot = $uninstallRoot
    UninstallKeyName = $uninstallKeyName
    UninstallKeyPrefix = '{15B09250-AC39-4792-A15A-B73BD8E218A1}_is'
    UninstallKeyPath = Join-Path $uninstallRoot $uninstallKeyName
    SmokeHome = $SmokeHome
    ResultPath = Join-Path $SmokeHome $ResultFileName
  }
}

function Get-AppUninstallKeys {
  param(
    [Parameter(Mandatory)] [psobject] $Context,
    [switch] $Prefix
  )

  if (-not (Test-Path -LiteralPath $Context.UninstallRoot)) {
    return
  }
  if ($Prefix) {
    Get-ChildItem -LiteralPath $Context.UninstallRoot |
      Where-Object { $_.PSChildName.StartsWith($Context.UninstallKeyPrefix, [StringComparison]::OrdinalIgnoreCase) }
  } else {
    Get-ChildItem -LiteralPath $Context.UninstallRoot |
      Where-Object { $_.PSChildName -eq $Context.UninstallKeyName }
  }
}

function Assert-Installed {
  param(
    [Parameter(Mandatory)] [psobject] $Context,
    [AllowNull()] [string] $ExpectedVersion,
    [switch] $AllAppKeys
  )

  Assert-Condition (Test-Path -LiteralPath $Context.InstalledExe -PathType Leaf) "Installed executable missing: $($Context.InstalledExe)"
  $keys = @(if ($AllAppKeys) {
    Get-AppUninstallKeys -Context $Context -Prefix
  } else {
    Get-AppUninstallKeys -Context $Context
  })
  Assert-Condition ($keys.Count -eq 1) "Expected exactly one HKCU uninstall entry for $($Context.UninstallKeyName); found $($keys.Count)."
  Assert-Condition ($keys[0].PSChildName -eq $Context.UninstallKeyName) "Unexpected uninstall key: $($keys[0].PSChildName)"
  $entry = Get-ItemProperty -LiteralPath $keys[0].PSPath
  $actualDir = [System.IO.Path]::TrimEndingDirectorySeparator(
    [System.IO.Path]::GetFullPath([string] $entry.InstallLocation)
  )
  $expectedDir = [System.IO.Path]::TrimEndingDirectorySeparator(
    [System.IO.Path]::GetFullPath($Context.InstallDir)
  )
  Assert-Condition ($actualDir -ieq $expectedDir) "Unexpected InstallLocation: $actualDir"
  if ($null -ne $ExpectedVersion) {
    $actualVersion = [string] $entry.DisplayVersion
    Assert-Condition ($actualVersion -eq $ExpectedVersion) "Unexpected DisplayVersion: $actualVersion"
  }
  return $entry
}

function Invoke-RegisteredUninstall {
  param(
    [Parameter(Mandatory)] [psobject] $Context,
    [AllowNull()] [string] $ExpectedVersion,
    [Parameter(Mandatory)] [string] $LogPath,
    [switch] $AllAppKeys
  )

  $entry = Assert-Installed -Context $Context -ExpectedVersion $ExpectedVersion -AllAppKeys:$AllAppKeys
  $uninstallers = @(
    Get-ChildItem -LiteralPath $Context.InstallDir -Filter 'unins*.exe' -File |
      Where-Object { $_.Name -match '^unins[0-9]+\.exe$' }
  )
  Assert-Condition ($uninstallers.Count -eq 1) "Expected one registered unins*.exe; found $($uninstallers.Count)."
  $uninstaller = $uninstallers[0].FullName
  $command = [string] $entry.UninstallString
  Assert-Condition (
    $command.IndexOf($uninstaller, [StringComparison]::OrdinalIgnoreCase) -ge 0
  ) "UninstallString does not register $uninstaller`: $command"
  $arguments = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG="{0}"' -f $LogPath
  [void] (Invoke-BoundedProcess -Label 'Uninstall' -FilePath $uninstaller -Arguments $arguments -WorkingDirectory $Context.InstallDir -TimeoutSeconds 180)
}

function Assert-Uninstalled {
  param(
    [Parameter(Mandatory)] [psobject] $Context,
    [switch] $AllAppKeys
  )

  Assert-Condition (-not (Test-Path -LiteralPath $Context.InstallDir)) "Install directory survived uninstall: $($Context.InstallDir)"
  $keys = @(if ($AllAppKeys) {
    Get-AppUninstallKeys -Context $Context -Prefix
  } else {
    Get-AppUninstallKeys -Context $Context
  })
  Assert-Condition ($keys.Count -eq 0) "HKCU uninstall entry survived uninstall: $($Context.UninstallKeyName)"
}

function Assert-InstallerResult {
  param(
    [Parameter(Mandatory)] [string] $ResultPath,
    [Parameter(Mandatory)] [string] $ExpectedVersion
  )

  Assert-Condition (Test-Path -LiteralPath $ResultPath -PathType Leaf) "Installer smoke result missing: $ResultPath"
  $utf8 = [System.Text.UTF8Encoding]::new($false)
  $expected = $utf8.GetBytes("ANKI_MINER_INSTALLER_READY $ExpectedVersion`n")
  $actual = [System.IO.File]::ReadAllBytes($ResultPath)
  Assert-Condition (
    [Convert]::ToBase64String($actual) -ceq [Convert]::ToBase64String($expected)
  ) 'Installer smoke result was not the exact expected UTF-8 payload.'
}

function Get-TreeSnapshot {
  param(
    [Parameter(Mandatory)] [string] $Root,
    [Parameter(Mandatory)] [string] $Label
  )

  Assert-Condition (Test-Path -LiteralPath $Root -PathType Container) "$Label is missing."
  $fullRoot = [System.IO.Path]::GetFullPath($Root)
  foreach ($item in @(Get-ChildItem -LiteralPath $fullRoot -Recurse -Force | Sort-Object FullName)) {
    $relative = [System.IO.Path]::GetRelativePath($fullRoot, $item.FullName).Replace('\', '/')
    if ($item.PSIsContainer) {
      "D`t$relative"
    } else {
      $bytes = [System.IO.File]::ReadAllBytes($item.FullName)
      $hash = [Convert]::ToHexString([System.Security.Cryptography.SHA256]::HashData($bytes))
      "F`t$relative`t$($bytes.Length)`t$hash"
    }
  }
}

function Assert-TreeSnapshot {
  param(
    [Parameter(Mandatory)] [string] $Root,
    [Parameter(Mandatory)] [string[]] $Expected,
    [Parameter(Mandatory)] [string] $Label
  )

  $actual = @(Get-TreeSnapshot -Root $Root -Label $Label)
  Assert-Condition ($actual.Count -eq $Expected.Count) "$Label item count changed: expected $($Expected.Count), got $($actual.Count)."
  for ($index = 0; $index -lt $Expected.Count; $index++) {
    Assert-Condition (
      [string]::Equals($actual[$index], $Expected[$index], [StringComparison]::Ordinal)
    ) "$Label changed at snapshot item $index."
  }
}

function Get-SmokeHomeSnapshot {
  param([Parameter(Mandatory)] [string] $SmokeHome)
  Get-TreeSnapshot -Root $SmokeHome -Label 'ANKI_MINER_HOME'
}

function Assert-SmokeHomeSnapshot {
  param(
    [Parameter(Mandatory)] [string] $SmokeHome,
    [Parameter(Mandatory)] [string[]] $Expected
  )
  Assert-TreeSnapshot -Root $SmokeHome -Expected $Expected -Label 'ANKI_MINER_HOME'
}

function Assert-CleanInstallerRunner {
  param(
    [Parameter(Mandatory)] [psobject] $Context,
    [switch] $AllAppKeys,
    [switch] $IncludeLegacyShortcut
  )

  Assert-Condition ([Environment]::Is64BitProcess) 'Installer smoke requires x64 PowerShell for the 64-bit HKCU uninstall view.'
  Assert-Condition (-not (Test-Path -LiteralPath $Context.InstallDir)) "Dirty runner install directory: $($Context.InstallDir)"
  $keys = @(if ($AllAppKeys) {
    Get-AppUninstallKeys -Context $Context -Prefix
  } else {
    Get-AppUninstallKeys -Context $Context
  })
  Assert-Condition ($keys.Count -eq 0) "Dirty runner uninstall entry: $($Context.UninstallKeyName)"
  Assert-Condition (-not (Test-Path -LiteralPath $Context.DesktopShortcut)) "Dirty runner desktop shortcut: $($Context.DesktopShortcut)"
  Assert-Condition (-not (Test-Path -LiteralPath $Context.StartMenuGroup)) "Dirty runner Start Menu group: $($Context.StartMenuGroup)"
  Assert-Condition (-not (Test-Path -LiteralPath $Context.SmokeHome)) "Dirty runner smoke home: $($Context.SmokeHome)"
  if ($IncludeLegacyShortcut) {
    Assert-Condition (-not (Test-Path -LiteralPath $Context.LegacyShortcut)) "Dirty runner legacy shortcut: $($Context.LegacyShortcut)"
  }
}

function Remove-InstallerSmokePaths {
  param(
    [Parameter(Mandatory)] [string[]] $Paths,
    [Parameter(Mandatory)] [System.Collections.Generic.List[string]] $CleanupFailures
  )

  foreach ($target in $Paths) {
    if ([string]::IsNullOrWhiteSpace($target)) {
      continue
    }
    try {
      if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
      }
    } catch {
      [void] $CleanupFailures.Add("Remove ${target}: $($_.Exception.Message)")
    }
  }
}

function Resolve-OldInstaller {
  param(
    [Parameter(Mandatory)] [AllowEmptyCollection()] [object[]] $Releases,
    [Parameter(Mandatory)] [string] $ExpectedVersion
  )

  Assert-Condition ($ExpectedVersion -match '^\d+\.\d+\.\d+$') "Expected version is not X.Y.Z: $ExpectedVersion"
  if ($Releases.Count -eq 0) {
    return [pscustomobject]@{
      Kind = 'Skip'
      Reason = 'no-releases'
      Tag = $null
      Version = $null
      Asset = $null
    }
  }

  $currentVersion = [version] $ExpectedVersion
  $eligible = [System.Collections.Generic.List[object]]::new()
  foreach ($release in $Releases) {
    if ([bool] $release.draft -or [bool] $release.prerelease) {
      continue
    }
    $tag = [string] $release.tag
    if ($tag -notmatch '^v(\d+\.\d+\.\d+)$') {
      continue
    }
    $candidateVersionText = $Matches[1]
    $candidateVersion = [version] $candidateVersionText
    if ($candidateVersion -ge $currentVersion) {
      continue
    }
    $expectedAssetName = "AnkiMiner-$candidateVersionText-Windows-x86_64-Setup.exe"
    $assets = @($release.assets | Where-Object { [string] $_.name -ceq $expectedAssetName })
    if ($assets.Count -ne 1) {
      continue
    }
    [void] $eligible.Add([pscustomobject]@{
      Kind = 'Selected'
      Reason = $null
      Tag = $tag
      Version = $candidateVersionText
      ParsedVersion = $candidateVersion
      Asset = $assets[0]
    })
  }

  if ($eligible.Count -eq 0) {
    return [pscustomobject]@{
      Kind = 'Skip'
      Reason = 'no-eligible-version'
      Tag = $null
      Version = $null
      Asset = $null
    }
  }
  return @($eligible | Sort-Object ParsedVersion -Descending)[0]
}

function Assert-OldInstallerClassifierSelfTest {
  $eligibleListing = @(
    [pscustomobject]@{
      tag = 'v2.9.0'
      draft = $false
      prerelease = $false
      assets = @([pscustomobject]@{ name = 'AnkiMiner-2.9.0-Windows-x86_64-Setup.exe'; size = 10; id = 1 })
    },
    [pscustomobject]@{
      tag = 'v2.10.0'
      draft = $false
      prerelease = $false
      assets = @([pscustomobject]@{ name = 'AnkiMiner-2.10.0-Windows-x86_64-Setup.exe'; size = 11; id = 2 })
    },
    [pscustomobject]@{
      tag = 'v2.11.0'
      draft = $true
      prerelease = $false
      assets = @([pscustomobject]@{ name = 'AnkiMiner-2.11.0-Windows-x86_64-Setup.exe'; size = 12; id = 3 })
    }
  )
  $picked = Resolve-OldInstaller -Releases $eligibleListing -ExpectedVersion '3.0.0'
  Assert-Condition ($picked.Kind -eq 'Selected' -and $picked.Version -eq '2.10.0' -and $picked.Asset.id -eq 2) 'Old-installer classifier eligible-pick self-test failed.'

  $empty = Resolve-OldInstaller -Releases @() -ExpectedVersion '3.0.0'
  Assert-Condition ($empty.Kind -eq 'Skip' -and $empty.Reason -eq 'no-releases') 'Old-installer classifier empty-list self-test failed.'

  $driftListing = @(
    [pscustomobject]@{
      tag = 'v2.10.0'
      draft = $false
      prerelease = $false
      assets = @([pscustomobject]@{ name = 'AnkiMiner-Windows-2.10.0-Setup.exe'; size = 11; id = 4 })
    }
  )
  $drift = Resolve-OldInstaller -Releases $driftListing -ExpectedVersion '3.0.0'
  Assert-Condition ($drift.Kind -eq 'Skip' -and $drift.Reason -eq 'no-eligible-version') 'Old-installer classifier asset-drift self-test failed.'
}

function Get-GitHubReleases {
  param(
    [Parameter(Mandatory)] [string] $Repository,
    [Parameter(Mandatory)] [string] $WorkingDirectory
  )

  Assert-Condition ($Repository -match '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') "Invalid GITHUB_REPOSITORY: $Repository"
  $gh = (Get-Command gh -ErrorAction Stop).Source
  $arguments = 'api --paginate --slurp "repos/{0}/releases?per_page=100"' -f $Repository
  $lastFailure = $null
  for ($attempt = 1; $attempt -le 3; $attempt++) {
    try {
      $response = Invoke-BoundedProcess -Label "List GitHub releases (attempt $attempt/3)" -FilePath $gh -Arguments $arguments -WorkingDirectory $WorkingDirectory -TimeoutSeconds 120
      Assert-Condition (-not [string]::IsNullOrWhiteSpace($response.StdOut)) 'GitHub release listing returned an empty response.'
      Assert-Condition ($response.StdOut.TrimStart().StartsWith('[')) 'GitHub release listing did not return a JSON array.'
      $pages = ConvertFrom-Json -InputObject $response.StdOut -NoEnumerate -Depth 20
      $normalized = [System.Collections.Generic.List[object]]::new()
      foreach ($page in $pages) {
        foreach ($release in $page) {
          $assets = @(
            foreach ($asset in @($release.assets)) {
              $digestProperty = $asset.PSObject.Properties['digest']
              $digest = $null
              if ($null -ne $digestProperty -and $null -ne $digestProperty.Value) {
                $digest = [string] $digestProperty.Value
              }
              [pscustomobject]@{
                name = [string] $asset.name
                size = [int64] $asset.size
                digest = $digest
                id = [int64] $asset.id
              }
            }
          )
          [void] $normalized.Add([pscustomobject]@{
            tag = [string] $release.tag_name
            draft = [bool] $release.draft
            prerelease = [bool] $release.prerelease
            assets = $assets
          })
        }
      }
      return @($normalized)
    } catch {
      $lastFailure = $_
      if ($attempt -lt 3) {
        Start-Sleep -Seconds ([int] [Math]::Pow(2, $attempt))
      }
    }
  }
  throw "GitHub release listing failed after 3 attempts: $($lastFailure.Exception.Message)"
}

function Invoke-GitHubAssetDownloadAttempt {
  param(
    [Parameter(Mandatory)] [string] $GhPath,
    [Parameter(Mandatory)] [string] $Repository,
    [Parameter(Mandatory)] [int64] $AssetId,
    [Parameter(Mandatory)] [string] $Destination,
    [Parameter(Mandatory)] [string] $WorkingDirectory,
    [Parameter(Mandatory)] [int] $TimeoutSeconds
  )

  $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
  $startInfo.FileName = $GhPath
  $startInfo.Arguments = 'api -H "Accept: application/octet-stream" "repos/{0}/releases/assets/{1}"' -f $Repository, $AssetId
  $startInfo.WorkingDirectory = $WorkingDirectory
  $startInfo.UseShellExecute = $false
  $startInfo.CreateNoWindow = $true
  $startInfo.RedirectStandardOutput = $true
  $startInfo.RedirectStandardError = $true

  $process = [System.Diagnostics.Process]::new()
  $process.StartInfo = $startInfo
  $file = $null
  try {
    Assert-Condition ($process.Start()) 'GitHub release asset download failed to start.'
    $file = [System.IO.File]::Open($Destination, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    $copyTask = $process.StandardOutput.BaseStream.CopyToAsync($file)
    $stderrTask = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
      try {
        $process.Kill($true)
      } catch {
        Write-Warning "GitHub asset download process-tree kill failed: $($_.Exception.Message)"
      }
      [void] $process.WaitForExit(10000)
      throw "GitHub release asset download timed out after $TimeoutSeconds seconds."
    }
    $process.WaitForExit()
    $copyTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()
    $file.Flush()
    if ($process.ExitCode -ne 0) {
      throw "GitHub release asset download exited $($process.ExitCode).`nstderr:`n$stderr"
    }
  } finally {
    if ($null -ne $file) {
      $file.Dispose()
    }
    $process.Dispose()
  }
}

function Save-GitHubReleaseAsset {
  param(
    [Parameter(Mandatory)] [string] $Repository,
    [Parameter(Mandatory)] [psobject] $Asset,
    [Parameter(Mandatory)] [string] $Destination,
    [Parameter(Mandatory)] [string] $WorkingDirectory
  )

  $gh = (Get-Command gh -ErrorAction Stop).Source
  $lastFailure = $null
  for ($attempt = 1; $attempt -le 3; $attempt++) {
    try {
      if (Test-Path -LiteralPath $Destination) {
        Remove-Item -LiteralPath $Destination -Force
      }
      Invoke-GitHubAssetDownloadAttempt -GhPath $gh -Repository $Repository -AssetId ([int64] $Asset.id) -Destination $Destination -WorkingDirectory $WorkingDirectory -TimeoutSeconds 300
      $actualSize = (Get-Item -LiteralPath $Destination).Length
      $actualSha256 = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant()
      Write-Host "Old installer SHA-256: $actualSha256"
      Assert-Condition ($actualSize -eq [int64] $Asset.size) "Downloaded old installer size mismatch: expected $($Asset.size), got $actualSize."
      if (-not [string]::IsNullOrWhiteSpace([string] $Asset.digest)) {
        $digest = [string] $Asset.digest
        Assert-Condition ($digest -match '^sha256:([0-9a-fA-F]{64})$') "Unsupported or malformed GitHub asset digest: $digest"
        Assert-Condition ($actualSha256 -ceq $Matches[1].ToLowerInvariant()) "Downloaded old installer digest mismatch: expected $digest, got sha256:$actualSha256."
      }
      return
    } catch {
      $lastFailure = $_
      if (Test-Path -LiteralPath $Destination) {
        Remove-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue
      }
      if ($attempt -lt 3) {
        Start-Sleep -Seconds ([int] [Math]::Pow(2, $attempt))
      }
    }
  }
  throw "GitHub release asset download failed after 3 attempts: $($lastFailure.Exception.Message)"
}

function Get-DistInfoDirectoryNames {
  param([Parameter(Mandatory)] [string] $Root)

  Assert-Condition (Test-Path -LiteralPath $Root -PathType Container) "Runtime directory missing: $Root"
  Get-ChildItem -LiteralPath $Root -Directory -Recurse -Filter '*.dist-info' |
    ForEach-Object { $_.Name } |
    Sort-Object -Unique
}

function Assert-StringSetEqual {
  param(
    [Parameter(Mandatory)] [AllowEmptyCollection()] [string[]] $Actual,
    [Parameter(Mandatory)] [AllowEmptyCollection()] [string[]] $Expected,
    [Parameter(Mandatory)] [string] $Label
  )

  $actualSorted = @($Actual | Sort-Object -Unique)
  $expectedSorted = @($Expected | Sort-Object -Unique)
  Assert-Condition ($actualSorted.Count -eq $expectedSorted.Count) "$Label count mismatch: expected $($expectedSorted.Count), got $($actualSorted.Count)."
  for ($index = 0; $index -lt $expectedSorted.Count; $index++) {
    Assert-Condition (
      [string]::Equals($actualSorted[$index], $expectedSorted[$index], [StringComparison]::Ordinal)
    ) "$Label mismatch at index $index`: expected $($expectedSorted[$index]), got $($actualSorted[$index])."
  }
}

function Invoke-WindowsInstallerSmokeLegs12 {
  param(
    [Parameter(Mandatory)] [string] $InstallerPath,
    [Parameter(Mandatory)] [string] $ExpectedVersion,
    [Parameter(Mandatory)] [string] $RepoRoot
  )

  $repoRootFull = [System.IO.Path]::GetFullPath($RepoRoot)
  $installer = [System.IO.Path]::GetFullPath((Join-Path $repoRootFull $InstallerPath))
  $smokeHome = Join-Path $env:RUNNER_TEMP 'am home 日本語'
  $context = New-InstallerSmokeContext -RepoRoot $repoRootFull -SmokeHome $smokeHome -ResultFileName 'installer-result.txt'
  $setup1Log = Join-Path $env:RUNNER_TEMP 'setup1.log'
  $uninstall1Log = Join-Path $env:RUNNER_TEMP 'uninstall1.log'
  $setup2Log = Join-Path $env:RUNNER_TEMP 'setup2.log'
  $uninstall2Log = Join-Path $env:RUNNER_TEMP 'uninstall2.log'
  $cleanupLog = Join-Path $env:RUNNER_TEMP 'uninstall-cleanup.log'

  Assert-Condition (Test-Path -LiteralPath $installer -PathType Leaf) "Installer missing: $installer"
  Assert-CleanInstallerRunner -Context $context

  $primaryFailure = $null
  $cleanupFailures = [System.Collections.Generic.List[string]]::new()
  try {
    $setupCommon = '/CURRENTUSER /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-'

    # Leg 1: explicitly select no optional tasks.
    $setup1Args = '{0} /TASKS="" /LOG="{1}"' -f $setupCommon, $setup1Log
    [void] (Invoke-BoundedProcess -Label 'Setup leg 1' -FilePath $installer -Arguments $setup1Args -WorkingDirectory $repoRootFull -TimeoutSeconds 300)
    [void] (Assert-Installed -Context $context -ExpectedVersion $ExpectedVersion)
    Assert-Condition (-not (Test-Path -LiteralPath $context.DesktopShortcut)) 'Desktop shortcut exists after /TASKS="" install.'

    Invoke-InstalledAppSmoke -FilePath $context.InstalledExe -WorkingDirectory $context.InstallDir -SmokeHome $context.SmokeHome -ResultPath $context.ResultPath -Version $ExpectedVersion
    Assert-InstallerResult -ResultPath $context.ResultPath -ExpectedVersion $ExpectedVersion
    $homeSnapshot = @(Get-SmokeHomeSnapshot -SmokeHome $context.SmokeHome)

    Invoke-RegisteredUninstall -Context $context -ExpectedVersion $ExpectedVersion -LogPath $uninstall1Log
    Assert-Uninstalled -Context $context
    Assert-Condition (-not (Test-Path -LiteralPath $context.DesktopShortcut)) 'Desktop shortcut appeared or survived leg 1 uninstall.'
    Assert-SmokeHomeSnapshot -SmokeHome $context.SmokeHome -Expected $homeSnapshot
    Assert-InstallerResult -ResultPath $context.ResultPath -ExpectedVersion $ExpectedVersion
    Write-Host 'INSTALLER_SMOKE_PASS leg1'

    # Leg 2: omit /TASKS so the script's default desktop task is exercised.
    $setup2Args = '{0} /LOG="{1}"' -f $setupCommon, $setup2Log
    [void] (Invoke-BoundedProcess -Label 'Setup leg 2' -FilePath $installer -Arguments $setup2Args -WorkingDirectory $repoRootFull -TimeoutSeconds 300)
    [void] (Assert-Installed -Context $context -ExpectedVersion $ExpectedVersion)
    Assert-Condition (Test-Path -LiteralPath $context.DesktopShortcut -PathType Leaf) 'Default desktop shortcut was not created.'

    Invoke-RegisteredUninstall -Context $context -ExpectedVersion $ExpectedVersion -LogPath $uninstall2Log
    Assert-Uninstalled -Context $context
    Assert-Condition (-not (Test-Path -LiteralPath $context.DesktopShortcut)) 'Desktop shortcut survived leg 2 uninstall.'
    Assert-SmokeHomeSnapshot -SmokeHome $context.SmokeHome -Expected $homeSnapshot
    Assert-InstallerResult -ResultPath $context.ResultPath -ExpectedVersion $ExpectedVersion
    Write-Host 'INSTALLER_SMOKE_PASS leg2'
  } catch {
    $primaryFailure = $_
  } finally {
    # Best-effort emergency uninstall, then remove only paths this clean
    # hosted runner proved absent before the smoke.
    try {
      $keys = @(Get-AppUninstallKeys -Context $context)
      if ($keys.Count -eq 1 -and (Test-Path -LiteralPath $context.InstallDir)) {
        Invoke-RegisteredUninstall -Context $context -ExpectedVersion $null -LogPath $cleanupLog
      }
    } catch {
      [void] $cleanupFailures.Add("Emergency uninstall: $($_.Exception.Message)")
    }
    Remove-InstallerSmokePaths -Paths @(
      $context.DesktopShortcut,
      $context.StartMenuGroup,
      $context.InstallDir,
      $context.UninstallKeyPath,
      $context.SmokeHome
    ) -CleanupFailures $cleanupFailures
  }

  foreach ($cleanupFailure in $cleanupFailures) {
    Write-Warning $cleanupFailure
  }
  if ($null -ne $primaryFailure) {
    throw $primaryFailure
  }
  if ($cleanupFailures.Count -ne 0) {
    throw 'Windows installer smoke cleanup failed.'
  }
}

function Invoke-WindowsInstallerSmokeLeg3 {
  param(
    [Parameter(Mandatory)] [string] $InstallerPath,
    [Parameter(Mandatory)] [string] $ProbeInstallerPath,
    [Parameter(Mandatory)] [string] $ExpectedVersion,
    [Parameter(Mandatory)] [string] $Repository,
    [Parameter(Mandatory)] [string] $RepoRoot
  )

  $repoRootFull = [System.IO.Path]::GetFullPath($RepoRoot)
  $installer = [System.IO.Path]::GetFullPath((Join-Path $repoRootFull $InstallerPath))
  $probeInstaller = [System.IO.Path]::GetFullPath((Join-Path $repoRootFull $ProbeInstallerPath))
  $smokeHome = Join-Path $env:RUNNER_TEMP 'am upgrade home 日本語'
  $context = New-InstallerSmokeContext -RepoRoot $repoRootFull -SmokeHome $smokeHome -ResultFileName 'installer-upgrade-result.txt'
  $setupOldLog = Join-Path $env:RUNNER_TEMP 'setup-old.log'
  $setupUpgradeLog = Join-Path $env:RUNNER_TEMP 'setup-upgrade.log'
  $setupDowngradeLog = Join-Path $env:RUNNER_TEMP 'setup-downgrade-probe.log'
  $uninstallUpgradeLog = Join-Path $env:RUNNER_TEMP 'uninstall-upgrade.log'
  $oldInstallerPath = $null
  $terminalMarker = $null
  $runnerProvedClean = $false

  Assert-OldInstallerClassifierSelfTest

  $primaryFailure = $null
  $cleanupFailures = [System.Collections.Generic.List[string]]::new()
  try {
    Assert-Condition (Test-Path -LiteralPath $installer -PathType Leaf) "Installer missing: $installer"
    Assert-Condition (Test-Path -LiteralPath $probeInstaller -PathType Leaf) "Downgrade-probe installer missing: $probeInstaller"

    $releases = @(Get-GitHubReleases -Repository $Repository -WorkingDirectory $repoRootFull)
    $selection = Resolve-OldInstaller -Releases $releases -ExpectedVersion $ExpectedVersion
    if ($selection.Kind -eq 'Skip') {
      $terminalMarker = "INSTALLER_SMOKE_SKIP leg3 reason=$($selection.Reason)"
    } else {
      $oldInstallerPath = Join-Path $env:RUNNER_TEMP ("leg3-old-{0}" -f [string] $selection.Asset.name)
      Save-GitHubReleaseAsset -Repository $Repository -Asset $selection.Asset -Destination $oldInstallerPath -WorkingDirectory $repoRootFull

      Assert-CleanInstallerRunner -Context $context -AllAppKeys -IncludeLegacyShortcut
      $runnerProvedClean = $true

      $setupCommon = '/CURRENTUSER /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-'
      $setupOldArgs = '{0} /LOG="{1}"' -f $setupCommon, $setupOldLog
      [void] (Invoke-BoundedProcess -Label 'Setup leg 3 old version' -FilePath $oldInstallerPath -Arguments $setupOldArgs -WorkingDirectory $repoRootFull -TimeoutSeconds 300)
      [void] (Assert-Installed -Context $context -ExpectedVersion $selection.Version -AllAppKeys)
      Assert-Condition (Test-Path -LiteralPath $context.DesktopShortcut -PathType Leaf) 'Old installer did not create its default desktop shortcut.'

      Copy-Item -LiteralPath $context.DesktopShortcut -Destination $context.LegacyShortcut
      Assert-Condition (Test-Path -LiteralPath $context.LegacyShortcut -PathType Leaf) 'Failed to plant legacy app-created shortcut.'

      [void] (New-Item -ItemType Directory -Path $context.SmokeHome)
      $utf8 = [System.Text.UTF8Encoding]::new($false)
      $seedConfig = [ordered]@{
        anki_deck_name = 'Upgrade Smoke Deck'
        last_known_version = $selection.Version
      } | ConvertTo-Json -Compress
      [System.IO.File]::WriteAllText((Join-Path $context.SmokeHome 'gui_config.json'), $seedConfig, $utf8)
      [System.IO.File]::WriteAllText((Join-Path $context.SmokeHome 'upgrade-home.canary'), 'upgrade-home-canary', $utf8)
      $preUpgradeHomeSnapshot = @(Get-SmokeHomeSnapshot -SmokeHome $context.SmokeHome)

      $orphanCanary = Join-Path $context.InstallDir '_internal\upgrade-orphan.canary'
      [System.IO.File]::WriteAllText($orphanCanary, 'upgrade-orphan-canary', $utf8)
      Assert-Condition (Test-Path -LiteralPath $orphanCanary -PathType Leaf) 'Failed to plant upgrade orphan canary.'

      $setupUpgradeArgs = '{0} /LOG="{1}"' -f $setupCommon, $setupUpgradeLog
      [void] (Invoke-BoundedProcess -Label 'Setup leg 3 upgrade overlay' -FilePath $installer -Arguments $setupUpgradeArgs -WorkingDirectory $repoRootFull -TimeoutSeconds 300)
      [void] (Assert-Installed -Context $context -ExpectedVersion $ExpectedVersion -AllAppKeys)
      Assert-Condition (-not (Test-Path -LiteralPath $orphanCanary)) 'Upgrade overlay retained _internal upgrade-orphan.canary.'

      $builtInternal = Join-Path $repoRootFull 'dist\AnkiMiner\_internal'
      $installedDistInfo = @(Get-DistInfoDirectoryNames -Root (Join-Path $context.InstallDir '_internal'))
      $builtDistInfo = @(Get-DistInfoDirectoryNames -Root $builtInternal)
      Assert-StringSetEqual -Actual $installedDistInfo -Expected $builtDistInfo -Label '*.dist-info directory-name set'
      Assert-SmokeHomeSnapshot -SmokeHome $context.SmokeHome -Expected $preUpgradeHomeSnapshot
      Assert-Condition (Test-Path -LiteralPath $context.DesktopShortcut -PathType Leaf) 'Upgrade did not restore the previous desktop task.'

      Invoke-InstalledAppSmoke -FilePath $context.InstalledExe -WorkingDirectory $context.InstallDir -SmokeHome $context.SmokeHome -ResultPath $context.ResultPath -Version $ExpectedVersion
      Assert-InstallerResult -ResultPath $context.ResultPath -ExpectedVersion $ExpectedVersion

      $configPath = Join-Path $context.SmokeHome 'gui_config.json'
      $configAfter = ConvertFrom-Json -InputObject ([System.IO.File]::ReadAllText($configPath)) -Depth 20
      Assert-Condition ([string] $configAfter.last_known_version -eq $ExpectedVersion) 'Upgrade launch did not update last_known_version.'
      Assert-Condition ($configAfter.first_run_setup_done -eq $true) 'Upgrade launch did not seed first_run_setup_done.'
      Assert-Condition ($configAfter.first_run_shortcut_done -eq $true) 'Upgrade launch did not seed first_run_shortcut_done.'
      Assert-Condition ([string] $configAfter.anki_deck_name -eq 'Upgrade Smoke Deck') 'Upgrade launch did not preserve anki_deck_name.'
      $postLaunchHomeSnapshot = @(Get-SmokeHomeSnapshot -SmokeHome $context.SmokeHome)

      $preDowngradeInstallSnapshot = @(Get-TreeSnapshot -Root $context.InstallDir -Label 'install tree')
      $setupDowngradeArgs = '{0} /LOG="{1}"' -f $setupCommon, $setupDowngradeLog
      $downgradeResult = Invoke-BoundedProcess -Label 'Setup leg 3 downgrade probe' -FilePath $probeInstaller -Arguments $setupDowngradeArgs -WorkingDirectory $repoRootFull -TimeoutSeconds 300 -AllowedExitCodes @(7)
      Assert-Condition ($downgradeResult.ExitCode -eq 7) "Downgrade probe returned $($downgradeResult.ExitCode), expected 7."
      Assert-TreeSnapshot -Root $context.InstallDir -Expected $preDowngradeInstallSnapshot -Label 'install tree'

      Invoke-RegisteredUninstall -Context $context -ExpectedVersion $ExpectedVersion -LogPath $uninstallUpgradeLog -AllAppKeys
      Assert-Uninstalled -Context $context -AllAppKeys
      Assert-Condition (-not (Test-Path -LiteralPath $context.StartMenuGroup)) 'Start Menu group survived leg 3 uninstall.'
      Assert-Condition (-not (Test-Path -LiteralPath $context.DesktopShortcut)) 'Desktop shortcut survived leg 3 uninstall.'
      Assert-Condition (-not (Test-Path -LiteralPath $context.LegacyShortcut)) 'Legacy user-profile shortcut survived leg 3 uninstall.'
      Assert-SmokeHomeSnapshot -SmokeHome $context.SmokeHome -Expected $postLaunchHomeSnapshot
      $terminalMarker = 'INSTALLER_SMOKE_PASS leg3'
    }
  } catch {
    $primaryFailure = $_
  } finally {
    if ($runnerProvedClean) {
      try {
        $keys = @(Get-AppUninstallKeys -Context $context -Prefix)
        if ($keys.Count -eq 1 -and (Test-Path -LiteralPath $context.InstallDir)) {
          Invoke-RegisteredUninstall -Context $context -ExpectedVersion $null -LogPath $uninstallUpgradeLog -AllAppKeys
        }
      } catch {
        [void] $cleanupFailures.Add("Emergency uninstall: $($_.Exception.Message)")
      }
      foreach ($key in @(Get-AppUninstallKeys -Context $context -Prefix)) {
        try {
          Remove-Item -LiteralPath $key.PSPath -Recurse -Force
        } catch {
          [void] $cleanupFailures.Add("Remove $($key.PSPath): $($_.Exception.Message)")
        }
      }
      Remove-InstallerSmokePaths -Paths @(
        $context.InstallDir,
        $context.DesktopShortcut,
        $context.LegacyShortcut,
        $context.StartMenuGroup,
        $context.SmokeHome
      ) -CleanupFailures $cleanupFailures
    }
    Remove-InstallerSmokePaths -Paths @($oldInstallerPath) -CleanupFailures $cleanupFailures
  }

  foreach ($cleanupFailure in $cleanupFailures) {
    Write-Warning $cleanupFailure
  }
  if ($null -ne $primaryFailure) {
    throw $primaryFailure
  }
  if ($cleanupFailures.Count -ne 0) {
    throw 'Windows installer smoke leg 3 cleanup failed.'
  }
  Assert-Condition (-not [string]::IsNullOrWhiteSpace($terminalMarker)) 'Windows installer smoke leg 3 produced no terminal marker.'
  Write-Host $terminalMarker
}
