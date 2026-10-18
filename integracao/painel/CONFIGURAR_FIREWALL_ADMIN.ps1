# Execute como Administrador do Windows somente depois de configurar o servidor.
# Restringe a porta HTTPS 8765 a dispositivos da sub-rede 192.168.0.0/24.
$ErrorActionPreference='Stop'
$nome='Sonifarma MVP HTTPS Rede Interna'
Get-NetFirewallRule -DisplayName $nome -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $nome -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765 -RemoteAddress '192.168.0.0/24' -Profile Private,Domain | Out-Null
Write-Host 'Regra criada. Verifique o perfil de rede e o acesso dos computadores autorizados.'
