<#
car.ps1 - the `car` shortcut on a Windows workstation: runs the car's own `car` command over SSH.

    car                      live dashboard          car drive         dashboard + keyboard driving
    car status | estop | reset | resume | stop       car sensors       live line + light readings
    car calib light|line|trim                        car logs          the car server's log (follow)
    car restart | version                            car wifi ...      hotspot control (admin account)
    car web                  open the app            car ssh | admin   a shell on the car (c4 | carops)
    car deploy [--dry-run]   deploy this checkout (docker compose, from the repository)

The car: $env:CAR_HOST (default picar.local; in hotspot mode 10.42.0.1).
Install: add this folder to PATH, or put   Set-Alias car <repo>\workstation\car.ps1   in your $PROFILE.
#>
$ErrorActionPreference = 'Stop'
$CarHost = if ($env:CAR_HOST) { $env:CAR_HOST } else { 'picar.local' }
$Repo = Split-Path -Parent $PSScriptRoot
$cmd = if ($args.Count -gt 0) { [string]$args[0] } else { 'watch' }
$rest = if ($args.Count -gt 1) { $args[1..($args.Count - 1)] } else { @() }
# a terminal for the dashboards and keys; plain output when redirected or scripted
$Tty = if ([Console]::IsInputRedirected) { @() } else { @('-t') }

switch ($cmd) {
    'web'    { Start-Process "http://${CarHost}:8080/"; break }
    'ssh'    { ssh "c4@$CarHost"; break }
    'admin'  { ssh "carops@$CarHost"; break }
    'deploy' {
        Push-Location $Repo
        try {
            if ($rest -contains '--dry-run') { docker compose --profile deploy run --rm deploy }
            else { docker compose --profile deploy run --rm deploy sh deploy/deploy.sh }
        } finally { Pop-Location }
        break
    }
    'wifi'   { ssh @Tty "carops@$CarHost" car wifi @rest; break }
    { $_ -in 'help', '-h', '--help' } { Get-Help $PSCommandPath; break }
    default  { ssh @Tty "c4@$CarHost" car $cmd @rest }
}
exit $LASTEXITCODE
