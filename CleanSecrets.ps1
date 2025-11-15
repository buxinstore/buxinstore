<# 
.SYNOPSIS
    Scans the repository for common secret patterns, prepares replacements.txt,
    rewrites history with git filter-repo, and force pushes the cleaned branch.

.USAGE
    powershell -ExecutionPolicy Bypass -File .\CleanSecrets.ps1

.NOTES
    This script is destructive (history rewrite + force push). Back up the repo first.
#>
param(
    [string]$RepoPath = "E:\update version\v11",
    [string]$BranchName = "main",
    [switch]$SkipBackupWarning
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host "[*] $Message" -ForegroundColor Cyan
}

function Write-WarningBlock {
    param([string]$Message)
    Write-Host "[!] $Message" -ForegroundColor Yellow
}

function Get-RelativePath {
    param(
        [string]$Root,
        [string]$FullPath
    )

    $normalizedRoot = (Resolve-Path -Path $Root).ProviderPath
    $normalizedFullPath = (Resolve-Path -LiteralPath $FullPath).ProviderPath
    $uriRoot = [System.Uri]::new("$normalizedRoot\")
    $uriFull = [System.Uri]::new($normalizedFullPath)
    return $uriRoot.MakeRelativeUri($uriFull).OriginalString -replace '/', '\'
}

function Initialize-ReplacementsFile {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Force
    }
    New-Item -ItemType File -Path $Path -Force | Out-Null
}

function Get-SecretPatterns {
    @(
        @{ Name = "AWS Access Key"; Regex = "AKIA[0-9A-Z]{16}" },
        @{ Name = "AWS Secret Key"; Regex = "(?i)aws(.{0,20})?(secret|access).{0,4}[\x27\x60:\x22=]{0,4}[A-Za-z0-9/+=]{40}" },
        @{ Name = "Generic Bearer Token"; Regex = "Bearer\s+[A-Za-z0-9\-\._~\+/]+=*" },
        @{ Name = "JWT"; Regex = "eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}" },
        @{ Name = "Google API Key"; Regex = "AIza[0-9A-Za-z\-_]{35}" },
        @{ Name = "Google OAuth Secret"; Regex = "(?i)google(.{0,20})?(secret|client).{0,4}[\x27\x60:\x22=]{0,4}[A-Za-z0-9\-_]{24,}" },
        @{ Name = "Stripe Key"; Regex = "(sk|pk)_(test|live)_[0-9a-zA-Z]{24,}" },
        @{ Name = "Slack Token"; Regex = "xox[baprs]-[0-9a-zA-Z]{10,48}" },
        @{ Name = "Private Key Block"; Regex = "-----BEGIN (RSA|DSA|EC|OPENSSH|PGP) PRIVATE KEY-----" },
        @{ Name = "Generic 32+ Char Secret"; Regex = "(?<![A-Za-z0-9])[A-Za-z0-9_\-]{32,}(?![A-Za-z0-9])" }
    )
}

function Should-SkipFile {
    param([System.IO.FileInfo]$File, [string[]]$BinaryExtensions)
    if ($File.FullName -like "*\.git\*") { return $true }
    if ($File.Name -ieq "replacements.txt") { return $true }
    if ($BinaryExtensions -contains $File.Extension.ToLowerInvariant()) { return $true }
    return $false
}

Write-WarningBlock "This operation rewrites history and force pushes to origin/$BranchName."
Write-WarningBlock "Create a backup or clone before proceeding."
if (-not $SkipBackupWarning) {
    $confirmation = Read-Host "Type YES to continue"
    if ($confirmation -ne "YES") {
        Write-WarningBlock "Aborting at user request."
        exit 1
    }
}

if (-not (Test-Path -LiteralPath $RepoPath)) {
    throw "Repo path '$RepoPath' was not found."
}

Push-Location -LiteralPath $RepoPath
try {
    if (-not (Test-Path -LiteralPath ".git")) {
        throw "The path '$RepoPath' is not a Git repository."
    }

    Write-Step "Validating required commands"
    git --version | Out-Null
    git filter-repo --version | Out-Null

    $replacementFile = Join-Path (Get-Location) "replacements.txt"
    Write-Step "Preparing replacements file at $replacementFile"
    Initialize-ReplacementsFile -Path $replacementFile

    $binaryExtensions = @(
        ".png",".jpg",".jpeg",".gif",".webp",".bmp",".ico",".pdf",".db",
        ".sqlite",".pyc",".pyo",".so",".dll",".exe",".woff",".woff2",".ttf",
        ".otf",".zip",".gz",".7z",".tar",".mp4",".mp3",".wav",".svgz"
    )
    $patterns = Get-SecretPatterns
    $files = Get-ChildItem -Recurse -File -ErrorAction SilentlyContinue
    $entries = [System.Collections.Generic.HashSet[string]]::new()
    $totalMatches = 0

    Write-Step "Scanning files for secrets"
    foreach ($file in $files) {
        if (Should-SkipFile -File $file -BinaryExtensions $binaryExtensions) {
            continue
        }

        try {
            $lineNumber = 0
            Get-Content -LiteralPath $file.FullName -ErrorAction Stop | ForEach-Object {
                $lineNumber++
                $line = $_
                foreach ($pattern in $patterns) {
                    if ($line -match $pattern.Regex) {
                        $relative = Get-RelativePath -Root (Get-Location) -FullPath $file.FullName
                        $entry = "$($relative):$lineNumber==>REMOVED_SECRET"
                        if ($entries.Add($entry)) {
                            Add-Content -LiteralPath $replacementFile -Value $entry
                            $totalMatches++
                            Write-Host ("    [match] {0} ({1}:{2})" -f $pattern.Name, $relative, $lineNumber) -ForegroundColor Green
                        }
                        break
                    }
                }
            }
        }
        catch {
            Write-WarningBlock ("Skipping unreadable file: {0} ({1})" -f $file.FullName, $_.Exception.Message)
        }
    }

    if ($totalMatches -eq 0) {
        Write-Step "No secrets detected with current patterns; replacements.txt will remain empty."
    }
    else {
        Write-Step "$totalMatches potential secret-containing lines added to replacements.txt"
    }

    Write-Step "Running git filter-repo (this may take a while)"
    git filter-repo --replace-text $replacementFile --force

    Write-Step "Force pushing cleaned history to origin/$BranchName"
    git push origin $BranchName --force

    Write-Step "Completed history rewrite and push."
}
finally {
    Pop-Location
}

