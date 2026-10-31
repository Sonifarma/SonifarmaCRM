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

## Reativar por WhatsApp dentro da ficha

Na ficha aberta pela Reativação, o botão do cabeçalho passa a **Reativar por WhatsApp**. A aba Produtos comprados também possui um painel de contato com Preparar mensagem, Preferência de contato, atualização do estado e resumo da última reativação. O formulário específico de reativação abre sobre a ficha, mantendo os produtos para avaliação ao voltar.

O fluxo é: registrar uma autorização já concedida quando necessário, revisar telefone e texto, abrir o WhatsApp e depois confirmar manualmente o envio. O contato é salvo no SQLite existente como `tipo_aviso=reativacao`, com cliente e referência da última compra. A confirmação mantém data/hora e atendente, valida o número utilizado e bloqueia novos contatos conforme o intervalo de três dias já aplicado pelo servidor. Abertura sozinha permanece como WhatsApp aberto. Recusa continua bloqueando o envio. Medicamentos da avaliação não são inseridos automaticamente na mensagem promocional.

O último contato aparece novamente ao reabrir a ficha; o histórico do Pós-venda e o histórico do consumidor mostram o tipo de contato e os dados da confirmação. A API local consulta a preferência e os registros atuais antes de preparar a mensagem. O pop-up do WhatsApp nasce no clique antes das consultas assíncronas, e o fechamento do diálogo retorna à ficha.

Atualize `index.html` e `iniciar_interface.py` e reinicie o servidor para aplicar. A funcionalidade reutiliza tabelas, autenticação, auditoria e regras de confirmação existentes. O teste `tests/test_reativacao_envio_browser.cjs` usa somente servidor e WhatsApp simulados; não envia mensagens reais.


### Processo iniciado sem listener confirmado

O controle identifica o Python que atende a porta, inclusive quando ele é filho do launcher de um ambiente virtual Windows. Um processo já iniciado também precisa passar pela verificação HTTPS com o certificado local. Processos sem vínculo com o PID registrado não são assumidos nem encerrados.

Se a confirmação falhar, consulte `sonifarma_servidor.log`, `sonifarma_erros.log` e os listeners da porta 8765. O endereço de rede esperado continua sendo `192.168.0.239`; a correção de identificação não resolve IP ausente, erro de conexão ao ERP ou certificado inválido.

Regressão do controle: `pwsh -NoProfile -File tests/test_controle_windows.ps1` na raiz do repositório. Os testes simulam CIM e TCP e não substituem a validação na instalação Windows.


## Favoritos, modelos e fila de atendimento

A Central, a Reativação e os Aniversariantes possuem favoritos por usuário. Salvar favorito captura os campos e seleções; aplicar restaura os filtros e orienta clicar em Consultar. Aplicar não apaga a lista já carregada. Intervalos relativos recalculam suas datas; intervalos personalizados mantêm as datas salvas.

Mensagens e cupons permite editar os modelos de aniversário e reativação e os dados do cupom. As variáveis são `{nome}`, `{cupom}`, `{beneficio}` e `{condicoes}`. Salvar modelos, ou Salvar cupom nos aniversariantes, persiste a configuração para a equipe na tabela crm_config do banco local agenda_posvenda.sqlite3, incluído nos backups existentes. Favoritos usam o login como proprietário. Configurações exigem autenticação e CSRF. Não há gravação no ERP.

O filtro de status atua somente nos clientes já carregados, indicando visíveis e total. Enviados confirmados inclui os que aguardam resposta. Aguardando resposta corresponde a envio confirmado com resultado Aguardando resposta. A consulta do status considera a recusa de contato, o último registro da campanha e o número sem WhatsApp registrado. Reativação usa contatos de reativação; aniversariantes usam o mês/ano da campanha; a Central considera o último contato geral do cliente. Carregue mais para examinar outros clientes.

Próximo cliente está disponível na ficha e no editor de aniversário/reativação. A fila respeita a ordem e o filtro visíveis no momento da abertura e preserva a lista consultada; ao chegar ao último, o botão fica desabilitado. Avançar não envia nem confirma mensagens. A paginação automática da Central não é acionada por trás de um modal aberto nem após alteração de filtros sem uma nova consulta.

Validação: testes de preferências em tests/test_crm_preferencias.py e fluxo completo em tests/test_crm_workflow_browser.cjs.

Os modelos opcionais de recompra e Farmácia Popular aceitam também `{produto}`, `{data}` e `{link}`. Em branco, mantêm a geração padrão existente. Os índices de status são criados apenas no SQLite local, sem alterar índices do ERP.


## Responsável e próxima ação

O botão Atendimento está na ficha e nos editores de mensagens. Permite assumir/liberar o cliente, registrar uma próxima ação com data/hora local e uma observação operacional. Os dados ficam em crm_atendimentos no banco agenda_posvenda.sqlite3 e entram no backup existente. O ERP continua somente para leitura.

Abrir um contato atribui automaticamente clientes sem responsável ao usuário que registrou a abertura. Um atendente não pode abrir novo contato nem alterar atendimento atribuído a outro. Supervisores e MASTER podem reatribuir para usuários ativos. A abertura verifica o responsável novamente dentro da transação que grava o contato, evitando a disputa entre dois atendentes.

Cada alteração usa uma revisão: quando outro usuário já salvou, a operação retorna conflito e o formulário mantém o rascunho. Atualizar carrega os dados atuais; copie alterações não salvas antes de atualizar. Operações são auditadas sem copiar a observação para o log.

A tela Retornos possui filtros Meus retornos, Hoje, Atrasados e Todos da equipe, combinados com Pendentes/Concluídos/Todos. As páginas têm 50 registros em ordem de data/hora. O menu mostra os retornos atrasados ou de hoje do usuário, atualizado ao entrar, ao salvar e a cada minuto enquanto a página estiver visível. Não há notificação quando o navegador está fechado.

Concluir ação mantém o histórico do último agendamento e o responsável; não confirma mensagem, compra ou resultado de campanha. Salvar uma nova ação reabre a pendência. Liberar deixa o cliente sem responsável, preservando o agendamento; ele pode ser encontrado em Todos da equipe. Os horários seguem o relógio local do servidor Windows.

Testes: tests/test_crm_atendimento.py e tests/test_crm_atendimento_browser.cjs.


## API oficial do WhatsApp

A integração opcional com a Cloud API está descrita em WHATSAPP_API.md. Não é habilitada por padrão. Use o .env local para credenciais e publique somente o webhook por HTTPS público. O modo manual permanece disponível. O botão Enviar pela API permite revisar modelos de texto aprovados e seus parâmetros antes de solicitar um envio individual. Aceitação da API não é entrega; delivered/read chegam pelo webhook assinado.

O módulo whatsapp_meta.py precisa acompanhar iniciar_interface.py nesta primeira atualização. O atualizador novo o inclui na lista de código. Sem o módulo, o painel continua manual e informa a API como indisponível. Nenhuma mensagem real foi enviada nos testes automatizados.


## Registro de autorização em lote

Em Mensagens e cupons, MASTER e supervisores podem aplicar Autorização de contato em lote, informando a origem e a data em que os clientes já concederam a autorização. A data inicial 31/10/2026 e origem Balcão correspondem à confirmação fornecida pelo responsável nesta solicitação; revise os campos em operações futuras.

A consulta abrange todos os clientes com compras válidas no ERP, sem os filtros da tela, em páginas de 1.000 IDs e sem limite total de clientes. Não grava no ERP. Antes de alterar as preferências locais, cria backup verificável dos bancos existentes em BACKUP_DESTINATION (ambiente ou .env), ou na pasta backups do painel. Se o backup ou a consulta falhar, não aplica uma lista parcial.

A operação preserva recusas atuais e autorizações anteriores; modifica apenas registros não informados ou ausentes, com histórico individual de origem, data e operador. Repetir não duplica o histórico dos já autorizados. Novos clientes futuros não recebem autorização automática. Nenhum envio de WhatsApp é executado. A tela mostra os totais e o caminho do backup; listas já abertas podem precisar de nova consulta para atualizar todos os registros visíveis.
