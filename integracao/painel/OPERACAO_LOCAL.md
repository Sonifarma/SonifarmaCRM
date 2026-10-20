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

## Ficha compacta e carregamento

Os contatos, última compra, total de compras e o estado do WhatsApp permanecem visíveis. Data de nascimento, sexo, e-mail e valores ficam em **Mais dados do cliente**. A tabela tem cabeçalho fixo e uma área maior de rolagem; o editor de quantidade mantém os mesmos controles.

A aba inicial solicita cadastro, previsões e resumo de envios em paralelo. O histórico é solicitado apenas ao abrir Histórico de compras ou Resumo e reutilizado enquanto a ficha permanecer aberta. Ordenar a lista não refaz a consulta dos envios. O resumo de envios usa um índice em memória por produto/ciclo e é atualizado após registrar ou confirmar contatos. **Atualizar ficha** recarrega os dados, inclusive alterações feitas por outro atendente. Ao fechar ou trocar de cliente, as requisições da ficha são canceladas no navegador e respostas antigas são descartadas; uma consulta já iniciada no servidor pode terminar normalmente.

Para aplicar esta otimização, substitua apenas o `index.html` e atualize o navegador com Ctrl+F5. Não exige alteração do ERP ou dos bancos locais.

O teste de navegador `tests/test_ficha_browser.cjs` usa dados fictícios e um servidor HTTP local. Requer Node.js, Playwright e Chromium disponíveis no ambiente de testes. Execute `node tests/test_ficha_browser.cjs`; opcionalmente, `PLAYWRIGHT_MODULE` indica a instalação do Playwright e `CHROMIUM_EXECUTABLE` indica o navegador. Ele verifica consultas evitadas ao ordenar, carregamento sob demanda, recuperação de falha, troca rápida de cliente e layout em tela pequena.

## Avisos de Farmácia Popular na coluna Último envio

Os tipos Farmácia Popular — amanhã/hoje são registrados para o cliente e a data de retirada, com identificador `FARMACIA_POPULAR`, e não para cada EAN de medicamento. A coluna agora considera esse aviso em todas as linhas do mesmo cliente/data prevista e identifica o tipo de contato abaixo da data de confirmação. Isso não significa envio individual de cada medicamento. Clientes e datas diferentes continuam separados; os contatos de recompra mantêm o vínculo por produto.

Depois da confirmação, o resumo da ficha é atualizado independentemente da listagem geral do Pós-venda. O resumo mantém os tipos amanhã/hoje separados antes de selecionar o contato mais recente. Em registros antigos sem data de confirmação, a API pode recuperar data e atendente do evento explícito Envio confirmado na auditoria local, pelo mesmo registro e cliente. Sem esse evento, permanece Data não registrada. A consulta não altera os bancos antigos nem usa a data de abertura como data de envio.

Esta correção exige atualizar `index.html` e `iniciar_interface.py` e reiniciar o servidor local.

## Identidade visual Sonifarma

O tema usa a referência visual enviada pela equipe: turquesa `#00a6ad`, laranja `#ff6900` e fundos claros. Botões com texto branco usam turquesa mais escuro (`#00777d`) para melhorar a leitura. Avisos, erros e confirmações mantêm cores próprias. O logo foi preparado a partir da imagem de referência, com fundo transparente, e está em `sonifarma-logo.png`; é carregado localmente no login e na navegação. O painel não depende do site externo, de fontes remotas ou de CDN para exibir a marca.

A atualização inclui o HTML, o servidor Python, a ferramenta de manutenção e o PNG. Na primeira atualização deste tema, a ferramenta antiga ainda não inclui o logo na lista de arquivos; após `update` e antes de `start`, copie explicitamente `sonifarma-logo.png` da pasta de origem para a instalação. Nas atualizações seguintes, a ferramenta nova também preserva e atualiza esse arquivo. Reinicie o servidor e use Ctrl+F5 para carregar o tema novo.

## Produtos comprados na reativação

Ao abrir um consumidor pela lista de Reativação, a ficha começa em **Produtos comprados**, sem herdar a janela de previsão, PBMs ou classificações da Central. A Central continua abrindo em Próximas compras. Atualizar ficha ou limpar filtros preserva a origem da reativação.

A aba nova consulta todos os períodos registrados no ERP, agrupa por ID de produto e mostra primeira/última compra, número de vendas distintas, quantidade original, devoluções, saldo líquido e valor líquido estimado. Embalagens diferentes do mesmo produto são agrupadas; as quantidades são embalagens vendidas e não comprimidos. Produtos totalmente devolvidos permanecem visíveis com saldo zero. Vendas canceladas, estornadas, sem data ou itens inválidos ficam excluídos conforme as regras de validade usadas pelo painel.

São carregados 50 produtos por página, com total visível e botão **Carregar mais produtos** até completar o resultado. Não existe o corte de 100 linhas do histórico recente nessa consulta. A ordem inicial é última compra mais recente; a ordenação pelos títulos considera os produtos já carregados. Uma falha de página mantém os produtos anteriores e oferece nova tentativa. O histórico detalhado antigo continua independente e limitado a 100 linhas; não é a fonte do resumo novo.

A consulta usa conexão PostgreSQL somente de leitura e os timeouts existentes. Não altera dados, índices nem esquema do ERP. Validação em PostgreSQL com dados fictícios pode ser executada com `node tests/test_produtos_sql.cjs`, tendo `@electric-sql/pglite` instalado no ambiente de testes; `PGLITE_MODULE` pode indicar sua instalação. O teste de navegador é `tests/test_reativacao_produtos_browser.cjs`, usando Playwright/Chromium como os demais testes. A conexão real deve ser conferida no servidor Windows da farmácia depois da atualização de `index.html` e `iniciar_interface.py` e reinicialização.
