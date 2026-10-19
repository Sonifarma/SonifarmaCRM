# Operação local no Windows

Estas mudanças não instalam nada no computador da farmácia por acesso remoto.
Antes de substituir arquivos, guarde uma cópia da instalação atual. Use Python 3.12 e instale `psycopg2-binary` conforme `../requirements.txt`; HTTPS também requer `python -m pip install cryptography`. Use o mesmo Python indicado pelo controle.

## Inicialização e recuperação

1. Execute `START_SONIFARMA.bat` com o usuário Windows que operará o painel. A credencial `.sonifarma_pg.cred` existente continua sendo usada. O controle agora prefere um `.venv` local antes dos caminhos antigos de Python, aceita caminhos com espaços e valida `/api/auth/status` por HTTPS, com o certificado local e sem desativar a verificação TLS.
2. Depois de confirmar o funcionamento, abra PowerShell na pasta do painel e execute:

```powershell
.\SONIFARMA_AUTOMATICO.ps1 instalar
```

A tarefa `SonifarmaPainel` inicia **no login desse usuário**, com privilégio normal. Ela verifica o processo a cada 30 segundos e tenta iniciá-lo novamente se encerrar. Falhas têm espera progressiva de até cinco minutos. Não inicia antes do login e não detecta todos os tipos de travamento. Ao reiniciar, as sessões dos atendentes terminam.

A credencial protegida depende do mesmo usuário Windows e da mesma máquina. Não altere a tarefa para SYSTEM ou outro usuário. Não altere a política de execução global; se a política corporativa bloquear scripts, solicite a assinatura/liberação dos arquivos ao administrador.

Para manutenção, remova a supervisão **antes** de parar o painel, para impedir que ele seja reiniciado automaticamente:

```powershell
.\SONIFARMA_AUTOMATICO.ps1 remover
.\SONIFARMA_CONTROLE.ps1 stop
```

## Backup e verificação

Os backups novos possuem manifesto SHA-256. Cada banco é copiado com a API do SQLite (incluindo dados confirmados no WAL), restaurado em diretório temporário e validado com `PRAGMA integrity_check`. A cópia é consistente por banco; não é uma transação única entre os quatro bancos. O ZIP só é publicado ao concluir a validação. Os backups automáticos locais mantêm a retenção original de 30 arquivos.

Para copiar automaticamente cada backup também a outro disco ou compartilhamento, acrescente ao `.env` local do painel ou da pasta `integracao`:

```dotenv
BACKUP_DESTINATION=D:\BackupsSonifarma
```

O usuário Windows do painel precisa ter permissão nesse destino. A cópia externa não tem exclusão automática; acompanhe o espaço disponível. Se ela falhar, o backup local continua disponível e o erro é reportado. Uma pasta no mesmo disco não protege contra perda desse disco. Os ZIPs contêm dados e hashes de senhas: restrinja o acesso ao destino e use um volume protegido.

Backup manual e ensaio de restauração, sem substituir a instalação:

```powershell
.\SONIFARMA_CONTROLE.ps1 backup -Destino 'D:\BackupsSonifarma'
.\SONIFARMA_CONTROLE.ps1 verify -Arquivo 'D:\BackupsSonifarma\sonifarma_backup_DATA.zip'
```

O comando `verify` aceita o formato novo com manifesto. Backups antigos permanecem intactos. Para recuperar dados após um incidente, pare a supervisão e o painel, preserve o estado atual e restaure os bancos verificados com orientação técnica; não copie bancos sobre um processo ativo. Credenciais e certificados devem ter backup separado e protegido, pois não estão nesses ZIPs.

## Atualização de código

Na primeira adoção dessas ferramentas, copie `manutencao.py`, os dois scripts PowerShell e o `iniciar_interface.py` atualizado para a instalação com o painel parado, preservando todos os dados. Nas atualizações seguintes:

1. Baixe o código do repositório e extraia-o em outra pasta. Confira a procedência e a versão.
2. Remova a tarefa de supervisão, como descrito acima.
3. Execute com a origem apontando para o **painel** do pacote novo:

```powershell
.\SONIFARMA_CONTROLE.ps1 update -Origem 'C:\Atualizacoes\SonifarmaCRM\integracao\painel' -Destino 'D:\BackupsSonifarma'
.\SONIFARMA_CONTROLE.ps1 start
```

O controle para apenas o processo identificado pelo PID e caminho; cancela se a porta continuar ocupada. O atualizador valida os Python do pacote e cria backup verificado dos bancos e cópia do código anterior antes de substituir uma lista explícita de arquivos de código. Não copia `.env`, credenciais, certificados, bancos, logs nem backups do pacote. Dependências novas precisam ser instaladas separadamente conforme as notas da versão. Se ocorrer erro de cópia, tenta restaurar os arquivos de código anteriores e informa a falha. Não é uma atualização transacional contra queda de energia.

4. Valide login, consulta ao ERP, agenda e preferências comerciais. Se houver regressão, mantenha o painel parado e recoloque os arquivos de código da pasta `codigo_DATA` criada no destino, sem reverter os bancos inadvertidamente.
5. Reinstale a supervisão após a validação.

O IP de rede e a porta continuam os originais nesta etapa. Uma alteração de IP também exige atualizar o certificado e a configuração de rede; não foi aplicada automaticamente.

## Validação do número no WhatsApp

A confirmação humana de envio no Pós-venda passa a validar o número utilizado para aquele cliente. Apenas abrir o WhatsApp ou ter autorização comercial não valida o telefone. O aviso passa a verde na ficha e nas telas de mensagem quando existe envio confirmado para o mesmo cliente e número completo. Alterar o telefone volta a exigir validação, exceto se esse outro número já tiver envio confirmado próprio.

Para essa funcionalidade, atualize **index.html e iniciar_interface.py**, com o painel parado, e reinicie. A coluna adicional no SQLite é criada automaticamente, sem apagar o histórico. É armazenado um hash do número, além dos quatro últimos dígitos já registrados, sem gravar o telefone completo no histórico. A migração não altera o ERP.

Registros antigos, que só possuem quatro últimos dígitos, continuam preservados, mas não validam um número completo por suposição. A indicação representa confirmação do atendente; não é consulta automática à Meta nem comprovação de entrega ou leitura. A autorização comercial e o intervalo de contato continuam sendo exigidos separadamente.

## Coluna Último envio

Na ficha do cliente, a aba Próximas compras mostra o último contato de cada produto/ciclo e mantém o botão WhatsApp ao lado. Os estados são Não enviado, Aberto · aguardando confirmação e Confirmado. Ao confirmar um novo envio, a data/hora e o atendente ficam registrados separadamente do resultado do atendimento. Alterar o resultado depois não altera essa data. O detalhe ao passar o mouse mostra atendente e final do telefone. Registros antigos sem data de confirmação mostram Data não registrada; a data de abertura não é usada como se fosse a de envio.

A consulta dessa coluna usa um resumo por cliente e ciclo, sem depender do limite de 200 registros da listagem geral. Se a consulta falhar, aparece Histórico indisponível em vez de presumir que não houve contato. Atualize o HTML e o servidor Python e reinicie o painel para aplicar; a migração local é aditiva.
