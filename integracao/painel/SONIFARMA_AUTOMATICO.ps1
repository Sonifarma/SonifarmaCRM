# Executar com o mesmo usuario Windows que cadastrou .sonifarma_pg.cred.
param([ValidateSet('instalar','remover','supervisionar')][string]$Acao='instalar')
$ErrorActionPreference='Stop'
$Pasta=Split-Path -Parent $MyInvocation.MyCommand.Path
$Controle=Join-Path $Pasta 'SONIFARMA_CONTROLE.ps1'
$Nome='SonifarmaPainel'
if($Acao -eq 'instalar'){
 if(-not (Test-Path (Join-Path $Pasta '.sonifarma_pg.cred'))){throw 'Execute START_SONIFARMA.bat primeiro, com este usuario.'}
 $Usuario=[Security.Principal.WindowsIdentity]::GetCurrent().Name
 $Argumentos='-NoProfile -NonInteractive -File "'+$MyInvocation.MyCommand.Path+'" supervisionar'
 $Action=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $Argumentos -WorkingDirectory $Pasta
 $Trigger=New-ScheduledTaskTrigger -AtLogOn -User $Usuario
 $Principal=New-ScheduledTaskPrincipal -UserId $Usuario -LogonType Interactive -RunLevel Limited
 $Settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
 if(Get-ScheduledTask -TaskName $Nome -ErrorAction SilentlyContinue){throw 'Tarefa ja existe; revise ou remova antes de reinstalar.'}
 Register-ScheduledTask -TaskName $Nome -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings | Out-Null
 Start-ScheduledTask -TaskName $Nome
 Write-Host 'Inicio automatico configurado no login deste usuario. Nao inicia antes do login.'
 exit
}
if($Acao -eq 'remover'){
 if(Get-ScheduledTask -TaskName $Nome -ErrorAction SilentlyContinue){
  Stop-ScheduledTask -TaskName $Nome
  Unregister-ScheduledTask -TaskName $Nome -Confirm:$false
 }
 Write-Host 'Supervisao removida. Use STOP_SONIFARMA.bat para parar o painel.'
 exit
}
# O controle reconhece o processo pelo PID e caminho; nao mata processos desconhecidos.
# Recupera encerramentos do processo; nao reinicia automaticamente um processo travado.
$Falhas=0
while($true){
 try {
  & $Controle start -SemInteracao -SemNavegador
  $Falhas=0
 } catch {
  $Falhas++
  # Sem mensagens de excecao: podem conter configuracao local sensivel.
  Add-Content -Path (Join-Path $Pasta 'supervisao.log') -Value ((Get-Date -Format s)+' Falha ao iniciar; confira configuracao e logs locais.')
 }
 # Backoff evita tentativas continuas se o ERP estiver indisponivel.
 Start-Sleep -Seconds ([Math]::Min(300,30*[Math]::Pow(2,[Math]::Min($Falhas,4))))
}
