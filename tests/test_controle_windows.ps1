$ErrorActionPreference='Stop'
$source=Join-Path $PSScriptRoot '../integracao/painel/SONIFARMA_CONTROLE.ps1'
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile($source,[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors | Out-String)}
foreach($f in $ast.FindAll({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst]},$false)){
 . ([scriptblock]::Create($f.Extent.Text))
}
$Pasta=Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
New-Item -ItemType Directory $Pasta | Out-Null
$PidFile=Join-Path $Pasta 'panel.pid'
$Script='C:\Sonifarma [local]\painel\iniciar_interface.py'
$SemNavegador=$true
function Get-CimInstance {param($ClassName,$Filter,$ErrorAction) $script:processes[[int]($Filter -replace 'ProcessId=','')]}
function Get-NetTCPConnection {param($LocalPort,$State,$ErrorAction) $script:listeners}
function Start-Sleep {param($Seconds)}
function Get-Python {'Mock-Health'}
function Mock-Health {$script:healthCalls++;$script:healthArgs=$args;$global:LASTEXITCODE=$script:healthExit}
function Start-Process {throw 'Unexpected process launch'}
function Stop-Process {param($Id,$ErrorAction) $script:stopped=$Id}
function Wait-Process {param($Id,$Timeout,$ErrorAction)}
function Assert($Condition,$Message){if(-not $Condition){throw $Message}}
function Process($Id,$Parent,$Path=$Script){[pscustomobject]@{ProcessId=$Id;ParentProcessId=$Parent;Name='python.exe';CommandLine=('python.exe "'+$Path+'" --erp --lan')}}
function Reset {
 $script:processes=@{100=(Process 100 1);200=(Process 200 100)}
 $script:listeners=@([pscustomobject]@{OwningProcess=200;LocalAddress='192.168.0.239'})
 $script:healthCalls=0;$script:healthExit=0;$script:stopped=0
 Set-Content $PidFile 100
}
function Must-FailStart {
 $failed=$false
 try {Start-Sonifarma}catch{$failed=$true}
 Assert $failed 'Unsafe listener was accepted'
 Assert ($script:healthCalls -eq 0) 'Health called for unrecognized listener'
}
try {
 Reset
 Assert ((Get-SonifarmaProcess).ProcessId -eq 200) 'Child not recognized'
 Start-Sonifarma
 Assert ((Get-Content $PidFile).Trim() -eq '200') 'Serving PID not saved'
 Assert ($healthCalls -eq 1) 'HTTPS health omitted'
 Assert ($healthArgs -contains 'https://192.168.0.239:8765') 'Wrong HTTPS target'
 Assert ($healthArgs -contains '--certificate') 'Certificate verification omitted'
 Stop-Sonifarma
 Assert ($stopped -eq 200) 'Wrong process stopped'
 Reset
 $listeners=@([pscustomobject]@{OwningProcess=100;LocalAddress='192.168.0.239'})
 Start-Sonifarma
 Assert ($healthCalls -eq 1) 'Direct listener failed'
 Reset
 $processes[200].ParentProcessId=150;$processes[150]=Process 150 100
 Assert ((Get-SonifarmaProcess).ProcessId -eq 200) 'Descendant not recognized'
 Reset
 $processes[200].ParentProcessId=999
 Must-FailStart
 Reset
 $processes[200]=Process 200 100 ($Script+'.bak')
 Must-FailStart
 Reset
 $listeners[0].LocalAddress='127.0.0.1'
 Must-FailStart
 Reset
 $processes[300]=Process 300 999
 $listeners=@([pscustomobject]@{OwningProcess=300;LocalAddress='127.0.0.1'})+$listeners
 Start-Sonifarma
 Assert ((Get-Content $PidFile).Trim() -eq '200') 'First unrelated listener masked panel'
 Reset
 Remove-Item $PidFile
 Assert ($null -eq (Get-SonifarmaProcess)) 'Unrecorded process adopted'
 Reset
 $healthExit=1;$failed=$false
 try {Start-Sonifarma}catch{$failed=$true}
 Assert $failed 'Failed HTTPS health accepted'
 $healthExit=0
 Start-Sonifarma
 Assert ($healthCalls -eq 2) 'Existing process skipped health retry'
 Write-Host 'PASS: parser and 9 Windows controller scenarios (mocked CIM/TCP).'
}finally{Remove-Item $Pasta -Recurse -Force}
