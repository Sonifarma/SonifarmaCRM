# Atendimento WhatsApp — preparação da IA

O motor da IA será escolhido depois. Esta etapa adiciona a caixa de entrada e a base de exemplos/políticas. Não faz treinamento de modelo, não envia conversas a provedores de IA e não responde automaticamente no WhatsApp.

## Novas mensagens

Ative a integração e o webhook oficial da Meta, como descrito em WHATSAPP_API.md. Os callbacks continuam exigindo assinatura válida e Phone Number ID correspondente. Mensagens recebidas ficam em ia_mensagens, dentro do banco local agenda_posvenda.sqlite3, incluído nos backups existentes. Eventos repetidos não duplicam mensagens nem apagam revisões.

A tela mostra texto, nome exibido, quatro dígitos finais, horário e estado do atendimento. O número completo do remetente não é armazenado nesta tabela. O texto recebido é armazenado localmente e pode conter dados pessoais; o acesso usa as credenciais do CRM. O vínculo ao cliente é feito somente quando o hash do telefone corresponde a um único cliente já registrado nos contatos; caso contrário, fica sem vínculo confirmado. Não é feita identificação por nome parecido.

Imagens, documentos e áudios ficam sinalizados para revisão humana. Não há download, transcrição ou interpretação automática de receitas. A triagem é preliminar por termos de atenção: questões clínicas, urgência e descadastro pedem tratamento humano; não é diagnóstico. Um pedido de descadastro precisa ser registrado na preferência de contato pela equipe. A marcação da caixa de entrada não modifica automaticamente o consentimento comercial.

## Atendimentos antigos do aplicativo

A Cloud API não baixa automaticamente todo o histórico do aplicativo WhatsApp Business. No celular, exporte uma conversa individual sem mídia, obtendo o .txt. Em Atendimento IA, Atualizar caixa de entrada libera a área administrativa a MASTER e supervisores. Informe o arquivo e os nomes de todos os atendentes exatamente como aparecem na exportação.

A importação reconhece formatos comuns Android/iOS, reúne pergunta/resposta e remove nomes dos participantes, CPF/CNPJ, telefone e email reconhecíveis. Essa remoção é uma primeira passagem, não uma garantia de anonimização completa: revise os exemplos antes de aprovar. O arquivo completo não é persistido. Exemplos clínicos, mídia e pares muito longos não entram na base. Arquivos de até 4 MB podem ser importados; se necessário, divida o histórico em arquivos menores. Importações repetidas não duplicam os mesmos exemplos. Nenhum exemplo é aprovado automaticamente.

Conversas antigas servem para estilo e estrutura do atendimento. Não trate preços, promoções, estoque ou prazos antigos como fatos atuais. Cadastre separadamente as políticas atuais: horários reais, entrega, formas de contato e encaminhamentos. Políticas e exemplos ficam pendentes até aprovação de MASTER/supervisor. Campos de políticas são destinados a informações da empresa; não inclua dados de clientes.

## Uso nesta etapa

Atualize a caixa de entrada e clique em Revisar. Consultar referências mostra somente políticas e exemplos aprovados. O mecanismo atual é uma busca local por termos; ele não gera uma nova resposta e não representa treinamento de modelo. A equipe escreve e salva o rascunho. Questões de atenção podem preparar um texto fixo de encaminhamento, sempre para revisão.

Salvar revisão ou Marcar para equipe não envia mensagem nem cria uma compra ou tarefa externa. O rascunho pode ser copiado para uso manual após revisão. A responsabilidade já atribuída ao cliente é respeitada, e revisões concorrentes são bloqueadas. Não há aprendizagem automática a partir de respostas novas: bons exemplos precisam passar pela curadoria da equipe.

## Próxima etapa

Depois de revisar uma amostra dos atendimentos e aprovar as políticas, escolheremos o motor (API ou local), avaliaremos respostas em casos reais anonimizados e definiremos quais assuntos poderão ser automatizados. Envios reais e funcionamento autônomo não fazem parte desta atualização.

## Atualização

A versão inclui ia_atendimento.py, que deve ser copiado da nova versão para o painel após update e antes de start na primeira instalação. O atualizador novo já o inclui na lista de código; não altera o .env nem o ERP.
