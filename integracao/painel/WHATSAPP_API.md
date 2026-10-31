# WhatsApp Business Platform — Cloud API oficial

O painel permanece no Windows local. As chamadas de envio saem por HTTPS para graph.facebook.com. Não há disparo em lote. Cada mensagem exige uma ação explícita no botão Enviar mensagem pela API.

## Configuração local

Adicione as variáveis de WHATSAPP_API.env.example ao `.env` do painel. Elas também podem ser definidas no ambiente do processo. O token precisa permitir mensagens e leitura dos modelos da conta WhatsApp Business (WABA). Obtenha Phone Number ID, WABA ID, versão Graph suportada e credenciais no aplicativo/WhatsApp Manager da Meta. O painel não recebe nem exibe tokens no navegador.

Mantenha WHATSAPP_ENABLED=false até concluir a configuração. Depois de preencher as credenciais, defina true e reinicie o painel. Em WhatsApp API, use Verificar configuração e Consultar modelos aprovados. A consulta é somente de leitura; não envia mensagens. A versão Graph é obrigatória e não é fixada automaticamente pelo código.

Os modelos precisam estar aprovados na Meta. Nesta etapa são aceitos cabeçalho de texto, corpo e rodapé, com parâmetros numéricos `{{1}}`, `{{2}}` etc. Modelos com mídia, botões e parâmetros nomeados não são listados. O texto livre do editor manual não substitui o modelo aprovado. A autorização local de contato é exigida em todos os envios pela API, além das regras existentes de responsável e duplicidade.

## Webhook

Publique **somente** `/api/whatsapp-meta/webhook` por um proxy ou túnel com domínio e certificado HTTPS públicos válidos. O IP privado e o certificado local atuais não servem como callback público da Meta. Não é necessário publicar as demais páginas ou APIs do CRM.

Cadastre a URL pública e WHATSAPP_WEBHOOK_VERIFY_TOKEN no aplicativo Meta, assine os eventos de mensagens da WABA e informe o App Secret no `.env`. O GET de verificação exige o token correto. Cada POST exige X-Hub-Signature-256 validado com HMAC-SHA256 sobre o corpo original. O Phone Number ID do evento e o destinatário também precisam corresponder ao envio local.

Esta fase trata status de envio; mensagens recebidas não são exibidas nem armazenadas em uma caixa de entrada. A assinatura válida não é substituída pelo token de verificação.

## Estados e reenvio

- Aceito pela API: a Meta devolveu um identificador; ainda não comprova entrega.
- Enviado pela API: recebido o status sent; ainda aguarda entrega ao destinatário.
- Entregue/Lido: webhook delivered/read; atualiza o Pós-venda e a validação do número. Não equivale a aceite de uma oferta.
- Falha na API: recusa explícita ou status failed; código registrado sem corpo de erro ou segredos.
- Resultado incerto: houve timeout, falha de transporte ou erro 5xx. Não há repetição automática. Aguarde o webhook e confira no WhatsApp Manager antes de qualquer novo contato. Não existe liberação automática desse bloqueio nesta fase.

Uma chave por tentativa evita repetir a chamada ao clicar novamente. Enquanto houver envio pendente/incerto no ciclo, outro envio manual ou pela API é bloqueado. O identificador da tentativa é enviado como biz_opaque_callback_data para correlacionar um webhook que chegue antes da resposta ou após um timeout. Eventos repetidos e fora de ordem não rebaixam entregue/lido. Um timeout do navegador também deve ser tratado como resultado incerto: use Atualizar status.

Os dados de acompanhamento ficam no banco local agenda_posvenda.sqlite3 e entram nos backups existentes. O token e o App Secret permanecem fora desses registros e da auditoria.

## Atualização inicial

Esta versão acrescenta whatsapp_meta.py. O atualizador antigo ainda não conhece esse arquivo: copie-o da nova versão para o painel **depois de update e antes de start**. Sem o módulo, o modo manual continua disponível e a API fica desabilitada. Nas próximas versões, manutencao.py o inclui automaticamente e recusa pacotes sem o módulo. Preserve o .env existente.

## Validação

Os testes test_whatsapp_meta.py usam uma Meta simulada: nenhuma mensagem real é enviada. A validação final exige as credenciais da conta, modelo aprovado, callback público e um número de teste autorizado. Configure primeiro em ambiente de teste da Meta.

Referência de protocolo: https://github.com/fbsamples/whatsapp-api-examples (apenas consulta; nenhum código dos exemplos foi incorporado).
