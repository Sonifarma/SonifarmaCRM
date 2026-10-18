param(
 [ValidateSet('start','stop','restart','status','backup','verify','update')][string]$Acao='status',
 [switch]$SemInteracao,
 [switch]$SemNavegador,
 [string]$Origem,
 [string]$Destino,
 [string]$Arquivo
)
$ErrorActionPreference='Stop'
$Pasta=Split-Path -Parent $MyInvocation.MyCommand.Path
$Script=Join-Path $Pasta 'iniciar_interface.py'
$PidFile=Join-Path $Pasta '.sonifarma_servidor.pid'
$SenhaFile=Join-Path $Pasta '.sonifarma_pg.cred'
$LogFile=Join-Path $Pasta 'sonifarma_servidor.log'
function Get-SonifarmaProcess {
 if(-not (Test-Path $PidFile)){return $null}
 $idProcesso=0
 if(-not [int]::TryParse((Get-Content $PidFile -Raw).Trim(),[ref]$idProcesso)){return $null}
 $p=Get-CimInstance Win32_Process -Filter "ProcessId=$idProcesso" -ErrorAction SilentlyContinue
 if($p -and $p.Name -match '^python(w)?\.exe$' -and $p.CommandLine -like "*$Script*"){return $p}
 return $null
}
function Get-Python {
 $caminhos=@((Join-Path $Pasta '.venv\Scripts\python.exe'), (Join-Path $Pasta '..\.venv\Scripts\python.exe'), (Join-Path $Pasta '..\..\.venv\Scripts\python.exe'), 'C:\Users\Administrador\AppData\Local\Programs\Python\Python312\python.exe', (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'))
 foreach($c in $caminhos){if(Test-Path $c){return $c}}
 $cmd=Get-Command python.exe -ErrorAction SilentlyContinue
 if($cmd){return $cmd.Source}
 throw 'Python não localizado. Ajuste o caminho em SONIFARMA_CONTROLE.ps1.'
}
function Get-PortListener {
 return @(Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1)
}
function Start-Sonifarma {
 $atual=Get-SonifarmaProcess
 if($atual){Write-Host "Sonifarma ja esta em execucao pelo controle. PID: $($atual.ProcessId)";return}
 $ocupada=Get-PortListener
 if($ocupada.Count -gt 0){
  $pidOcupante=$ocupada[0].OwningProcess
  $proc=Get-CimInstance Win32_Process -Filter "ProcessId=$pidOcupante" -ErrorAction SilentlyContinue
  Write-Host "ERRO: porta 8765 ja ocupada por PID $pidOcupante em $($ocupada[0].LocalAddress)."
  if($proc){Write-Host "Processo: $($proc.Name) | $($proc.CommandLine)"}
  Write-Host 'Pare o processo antigo com seguranca antes de iniciar a V20.4.2. Nenhum processo foi encerrado automaticamente.'
  throw 'Porta ocupada por processo fora do controle.'
 }
 if(-not (Test-Path $SenhaFile)){
  if($SemInteracao){throw 'Configure a credencial com START_SONIFARMA.bat antes de ativar o modo automatico.'}
  Write-Host 'Primeiro acesso: informe a senha do PostgreSQL uma unica vez.'
  $secure=Read-Host 'Senha PostgreSQL' -AsSecureString
  $secure | ConvertFrom-SecureString | Set-Content -Path $SenhaFile -Encoding ASCII
  Write-Host 'Credencial protegida para este usuario Windows.'
 }
 $conteudo=(Get-Content -Path $SenhaFile -Encoding UTF8 -ErrorAction Stop | Select-Object -First 1)
 $conteudo=$conteudo.Trim([char]0xFEFF).Trim()
 try {$segura=ConvertTo-SecureString -String $conteudo -ErrorAction Stop}
 catch {throw 'Credencial invalida ou criada por outro usuario Windows. Verifique o usuario Windows e a credencial protegida.'}
 $ptr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($segura)
 try {$env:SONIFARMA_PG_PASSWORD=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)}
 finally {[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)}
 try {
  $exe=Get-Python
  $p=Start-Process -FilePath $exe -ArgumentList @('-u', ('"'+$Script+'"'),'--erp','--lan') -WorkingDirectory $Pasta -PassThru -RedirectStandardOutput $LogFile -RedirectStandardError (Join-Path $Pasta 'sonifarma_erros.log')
  Set-Content -Path $PidFile -Value $p.Id -Encoding ascii
 } finally {Remove-Item Env:\SONIFARMA_PG_PASSWORD -ErrorAction SilentlyContinue}
 # Aguarda conexao ao ERP e inicializacao, sem presumir que tres segundos bastam.
 for($Tentativa=0;$Tentativa -lt 30;$Tentativa++){
  Start-Sleep -Seconds 1
  if(-not (Get-SonifarmaProcess)){break}
  $Pronto=Get-PortListener
  if($Pronto -and $Pronto[0].OwningProcess -eq $p.Id){break}
 }
 $atual=Get-SonifarmaProcess
 if(-not $atual){
  Write-Host 'ERRO: servidor encerrou. Veja sonifarma_erros.log.'
  throw 'Falha ao iniciar o painel.'
 }
 $listener=Get-PortListener
 if($listener.Count -eq 0 -or $listener[0].OwningProcess -ne $p.Id -or $listener[0].LocalAddress -eq '127.0.0.1'){
  Write-Host 'ATENCAO: processo iniciado, mas nao foi confirmado HTTPS no IP de rede. Confira os logs.'
  throw 'Listener HTTPS nao confirmado.'
 }
 $Url='https://'+$listener[0].LocalAddress+':8765'
 & (Get-Python) (Join-Path $Pasta 'manutencao.py') health --url $Url --certificate (Join-Path $Pasta 'sonifarma_tls.crt')
 if($LASTEXITCODE -ne 0){throw 'Painel iniciou, mas a resposta HTTPS nao foi validada. Verifique certificado e logs.'}
 Write-Host "Sonifarma HTTPS iniciada no IP $($listener[0].LocalAddress):8765. PID: $($p.Id)"
 if(-not $SemNavegador){Start-Process ('https://'+$listener[0].LocalAddress+':8765')}
}
function Stop-Sonifarma {
 $p=Get-SonifarmaProcess
 if(-not $p){Write-Host 'Nenhum processo Sonifarma identificado pelo controle.';return}
 Stop-Process -Id $p.ProcessId -ErrorAction Stop
 Remove-Item $PidFile -ErrorAction SilentlyContinue
 Write-Host 'Servidor Sonifarma encerrado.'
}
switch($Acao){
 'start'{Start-Sonifarma}
 'stop'{Stop-Sonifarma}
 'restart'{Stop-Sonifarma;Start-Sonifarma}
 'backup'{
  if(-not $Destino){throw 'Informe -Destino em outro disco ou pasta de backup.'}
  & (Get-Python) (Join-Path $Pasta 'manutencao.py') backup --destination $Destino
  if($LASTEXITCODE -ne 0){throw 'Backup falhou.'}
 }
 'verify'{
  if(-not $Arquivo){throw 'Informe -Arquivo com o ZIP de backup.'}
  & (Get-Python) (Join-Path $Pasta 'manutencao.py') verify --archive $Arquivo
  if($LASTEXITCODE -ne 0){throw 'Verificacao falhou.'}
 }
 'update'{
  if(-not $Origem -or -not $Destino){throw 'Informe -Origem (painel novo) e -Destino (backup).'}
  if(Get-ScheduledTask -TaskName 'SonifarmaPainel' -ErrorAction SilentlyContinue){throw 'Remova a tarefa automatica antes de atualizar; reinstale apos validar.'}
  Stop-Sonifarma
  if(@(Get-PortListener).Count -gt 0){throw 'Porta ocupada: atualizacao cancelada.'}
  & (Get-Python) (Join-Path $Pasta 'manutencao.py') update --source $Origem --destination $Destino
  if($LASTEXITCODE -ne 0){throw 'Atualizacao falhou; verifique o backup antes de reiniciar.'}
  Write-Host 'Codigo atualizado. Inicie e valide o painel; rollback de codigo disponivel no destino.'
 }
 'status'{$p=Get-SonifarmaProcess;if($p){Write-Host "Sonifarma ativa. PID: $($p.ProcessId)"}else{Write-Host 'Sonifarma nao iniciada por este gerenciador.'}}
}
