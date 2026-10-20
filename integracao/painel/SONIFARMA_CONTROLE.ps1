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
function Test-SonifarmaProcess($Processo) {
 if(-not $Processo -or $Processo.Name -notmatch '^python(w)?\.exe$'){return $false}
 $Caminho=[regex]::Escape($Script)
 # Caminho inteiro como argumento: nao confundir outro arquivo ou prefixo parecido.
 $Padrao='(?i)(?:^|\s)(?:"'+$Caminho+'"|'+$Caminho+')(?=\s|$)'
 return [regex]::IsMatch([string]$Processo.CommandLine,$Padrao)
}
function Get-RecordedSonifarmaProcess {
 if(-not (Test-Path $PidFile)){return $null}
 $idProcesso=0
 if(-not [int]::TryParse((Get-Content $PidFile -Raw).Trim(),[ref]$idProcesso)){return $null}
 $p=Get-CimInstance Win32_Process -Filter "ProcessId=$idProcesso" -ErrorAction SilentlyContinue
 if(Test-SonifarmaProcess $p){return $p}
 return $null
}
function Get-SonifarmaProcess {
 $Principal=Get-RecordedSonifarmaProcess
 if(-not $Principal){return $null}
 foreach($Porta in @(Get-PortListener)){
  $Dono=Get-CimInstance Win32_Process -Filter "ProcessId=$($Porta.OwningProcess)" -ErrorAction SilentlyContinue
  if(-not (Test-SonifarmaProcess $Dono)){continue}
  if($Dono.ProcessId -eq $Principal.ProcessId){return $Dono}
  # O Python de um venv no Windows pode manter um launcher e servir no filho.
  $Ancestral=$Dono;$Visitados=@{}
  for($Nivel=0;$Nivel -lt 8;$Nivel++){
   $Pai=[int]$Ancestral.ParentProcessId
   if($Pai -eq $Principal.ProcessId){return $Dono}
   if($Pai -le 0 -or $Visitados.ContainsKey($Pai)){break}
   $Visitados[$Pai]=$true
   $Ancestral=Get-CimInstance Win32_Process -Filter "ProcessId=$Pai" -ErrorAction SilentlyContinue
   if(-not $Ancestral){break}
  }
 }
 return $Principal
}
function Get-SonifarmaListener($Processo) {
 if(-not $Processo){return}
 Get-PortListener | Where-Object {$_.OwningProcess -eq $Processo.ProcessId -and $_.LocalAddress -eq '192.168.0.239'}
}
function Get-Python {
 $caminhos=@((Join-Path $Pasta '.venv\Scripts\python.exe'), (Join-Path $Pasta '..\.venv\Scripts\python.exe'), (Join-Path $Pasta '..\..\.venv\Scripts\python.exe'), 'C:\Users\Administrador\AppData\Local\Programs\Python\Python312\python.exe', (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'))
 foreach($c in $caminhos){if(Test-Path $c){return $c}}
 $cmd=Get-Command python.exe -ErrorAction SilentlyContinue
 if($cmd){return $cmd.Source}
 throw 'Python não localizado. Ajuste o caminho em SONIFARMA_CONTROLE.ps1.'
}
function Get-PortListener {
 return @(Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue)
}
function Start-Sonifarma {
 $atual=Get-SonifarmaProcess
 if($atual){
  $p=[pscustomobject]@{Id=$atual.ProcessId}
  Write-Host "Processo existente identificado. Conferindo HTTPS. PID: $($atual.ProcessId)"
 }else{
  $ocupada=@(Get-PortListener)
  if($ocupada.Count -gt 0){
   $pidOcupante=$ocupada[0].OwningProcess
   $proc=Get-CimInstance Win32_Process -Filter "ProcessId=$pidOcupante" -ErrorAction SilentlyContinue
   Write-Host "ERRO: porta 8765 ja ocupada por PID $pidOcupante em $($ocupada[0].LocalAddress)."
   if($proc){Write-Host "Processo: $($proc.Name)"}
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
 }
 # Aguarda conexao ao ERP e inicializacao, sem presumir que tres segundos bastam.
 for($Tentativa=0;$Tentativa -lt 30;$Tentativa++){
  Start-Sleep -Seconds 1
  $atual=Get-SonifarmaProcess
  if(-not $atual){break}
  $Pronto=@(Get-SonifarmaListener $atual)
  if($Pronto.Count -gt 0){break}
 }
 $atual=Get-SonifarmaProcess
 if(-not $atual){
  Write-Host 'ERRO: servidor encerrou. Veja sonifarma_erros.log.'
  throw 'Falha ao iniciar o painel.'
 }
 $listener=@(Get-SonifarmaListener $atual)
 if($listener.Count -eq 0){
  foreach($Porta in @(Get-PortListener)){Write-Host "Porta 8765: $($Porta.LocalAddress), PID $($Porta.OwningProcess). Painel identificado: PID $($atual.ProcessId)."}
  Write-Host 'ATENCAO: processo iniciado, mas nao foi confirmado HTTPS no IP de rede. Confira os logs.'
  throw 'Listener HTTPS nao confirmado.'
 }
 # Registra quem realmente atende a porta, em vez do launcher intermediario.
 Set-Content -Path $PidFile -Value $atual.ProcessId -Encoding ascii
 $Url='https://'+$listener[0].LocalAddress+':8765'
 & (Get-Python) (Join-Path $Pasta 'manutencao.py') health --url $Url --certificate (Join-Path $Pasta 'sonifarma_tls.crt')
 if($LASTEXITCODE -ne 0){throw 'Painel iniciou, mas a resposta HTTPS nao foi validada. Verifique certificado e logs.'}
 Write-Host "Sonifarma HTTPS iniciada no IP $($listener[0].LocalAddress):8765. PID: $($atual.ProcessId)"
 if(-not $SemNavegador){Start-Process ('https://'+$listener[0].LocalAddress+':8765')}
}
function Stop-Sonifarma {
 $p=Get-SonifarmaProcess
 if(-not $p){Write-Host 'Nenhum processo Sonifarma identificado pelo controle.';return}
 Stop-Process -Id $p.ProcessId -ErrorAction Stop
 Wait-Process -Id $p.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
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
 'status'{$p=Get-SonifarmaProcess;if($p){Write-Host "Processo Sonifarma identificado. PID: $($p.ProcessId)"}else{Write-Host 'Sonifarma nao iniciada por este gerenciador.'}}
}
