<#
.SYNOPSIS
    Generates the three winget manifests for a released version.

.DESCRIPTION
    Writes the version, defaultLocale and installer manifests that
    github.com/microsoft/winget-pkgs expects, filled in with the release's real
    SHA256 so nothing has to be copied by hand.

    The release workflow runs this and attaches the result to the GitHub release.
    To submit, copy the generated folder into a fork of winget-pkgs under
    manifests/d/DiGii/A3Rig/<version>/ and open a pull request.

.EXAMPLE
    ./make-winget-manifests.ps1 -Version 0.1.0 -Sha256 ABC... -OutputDir out
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Version,
    [Parameter(Mandatory)][string]$Sha256,
    [string]$OutputDir = 'winget-manifests',
    [string]$Repo = 'AtrixZockt/DiGii-A3Rig',
    [string]$AssetName = 'a3rig-windows-x64.zip'
)

$ErrorActionPreference = 'Stop'

$identifier = 'DiGii.A3Rig'
$schema = '1.6.0'
$installerUrl = "https://github.com/$Repo/releases/download/v$Version/$AssetName"
$releaseDate = (Get-Date).ToString('yyyy-MM-dd')

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

# --- version manifest -------------------------------------------------------------
@"
# yaml-language-server: `$schema=https://aka.ms/winget-manifest.version.$schema.schema.json

PackageIdentifier: $identifier
PackageVersion: $Version
DefaultLocale: en-US
ManifestType: version
ManifestVersion: $schema
"@ | Set-Content -Path (Join-Path $OutputDir "$identifier.yaml") -Encoding utf8

# --- defaultLocale manifest -------------------------------------------------------
@"
# yaml-language-server: `$schema=https://aka.ms/winget-manifest.defaultLocale.$schema.schema.json

PackageIdentifier: $identifier
PackageVersion: $Version
PackageLocale: en-US
Publisher: DiGii
PublisherUrl: https://github.com/$($Repo.Split('/')[0])
PublisherSupportUrl: https://github.com/$Repo/issues
PackageName: a3rig
PackageUrl: https://github.com/$Repo
License: MIT
LicenseUrl: https://github.com/$Repo/blob/HEAD/LICENSE
ShortDescription: Starts a local Arma 3 mod test environment - HEMTT dev build, two clients and a dedicated server - with one command.
Description: |-
  a3rig builds and launches a complete local Arma 3 multiplayer test environment from
  inside any HEMTT project: it runs the dev build, starts two game clients on separate
  Arma profiles and a dedicated server, then tails the server log.

  It reproduces HEMTT's mod resolution exactly, so every process loads the same mods in
  the same order, and it checks the server config up front for the settings that silently
  break a local rig.
Moniker: a3rig
Tags:
- arma
- arma3
- hemtt
- modding
ReleaseNotesUrl: https://github.com/$Repo/releases/tag/v$Version
ManifestType: defaultLocale
ManifestVersion: $schema
"@ | Set-Content -Path (Join-Path $OutputDir "$identifier.locale.en-US.yaml") -Encoding utf8

# --- installer manifest -----------------------------------------------------------
# zip + nested portable: winget unpacks the archive and puts a3rig.exe on PATH under
# its own shim directory, the same shape HEMTT uses.
@"
# yaml-language-server: `$schema=https://aka.ms/winget-manifest.installer.$schema.schema.json

PackageIdentifier: $identifier
PackageVersion: $Version
InstallerType: zip
NestedInstallerType: portable
NestedInstallerFiles:
- RelativeFilePath: a3rig\a3rig.exe
  PortableCommandAlias: a3rig
ReleaseDate: $releaseDate
Installers:
- Architecture: x64
  InstallerUrl: $installerUrl
  InstallerSha256: $($Sha256.ToUpper())
ManifestType: installer
ManifestVersion: $schema
"@ | Set-Content -Path (Join-Path $OutputDir "$identifier.installer.yaml") -Encoding utf8

Write-Host "Wrote manifests for $identifier $Version to $OutputDir" -ForegroundColor Green
Get-ChildItem $OutputDir | ForEach-Object { "  $($_.Name)" }
