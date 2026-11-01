# Atendimento WhatsApp com IA — pausado

Pausa solicitada pelo responsável em 01/11/2026. Retomar somente quando solicitado. Este documento registra o estado do trabalho; não habilita integrações nem envios.

## Já preparado no repositório

- CRM local Windows, com ERP somente para leitura.
- Integração opcional com a API oficial da Meta: modelos de texto aprovados, envio individual e webhook assinado de entrega/leitura.
- Tela Atendimento IA: caixa de entrada local, importação de conversas .txt individuais, exemplos pendentes de aprovação, políticas atuais e rascunhos revisados pela equipe.
- Referências locais por termos, com triagem preliminar e encaminhamento humano. Não há modelo treinado, motor de IA escolhido ou resposta automática.
- Base publicada até o commit 9f006bf; 61 testes de servidor e testes no navegador passaram na preparação.

A publicação do código não confirma instalação no Windows nem ativação da conta real da Meta. Não recebemos conversas antigas ou backup do celular e não houve envio real de mensagens nos testes.

## Decisões do responsável

- WhatsApp Business em celular Android.
- Usar a API oficial da Meta.
- A futura IA deverá receber assuntos variados, com limites ainda a definir e questões clínicas destinadas à equipe/farmacêutico.
- Escolher o motor da IA depois; preparar caixa de entrada e exemplos primeiro.
- Interesse em aproveitar o histórico, incluindo áudios, para preservar o estilo de atendimento.

## Lista para retomar

1. Confirmar quais versões foram instaladas no Windows e se o webhook público da Meta está ativo. Identificadores, token e App Secret devem ser configurados localmente; não registrar segredos neste documento.
2. No Android, localizar Android/media/com.whatsapp.w4b/WhatsApp Business/Databases e informar somente o nome/formato do backup encontrado. O caminho pode variar; ainda não foi confirmado no aparelho.
3. Identificar a disponibilidade da pasta Media, especialmente Voice Notes e Audio. Trabalhar com uma cópia dos arquivos existentes.
4. Avaliar compatibilidade e método de desbloqueio do backup para WhatsApp Chat Exporter. O backup criptografado exige acesso legítimo à chave/método compatível, mantido localmente. Não assumir que a ferramenta poderá ler todo o histórico ou que o parâmetro --business, documentado para iOS, se aplica ao Android.
5. Se não houver backup compatível, começar por conversas individuais exportadas com mídia. Confirmar os nomes dos atendentes que aparecem nos arquivos.
6. Selecionar a ferramenta de transcrição (candidata: faster-whisper) conforme CPU, RAM e GPU disponíveis. Testar português e os formatos de áudio reais antes de instalar no servidor de produção.
7. Integrar importação de pacote/mídias e transcrição em lote, preservando vínculo entre conversa, remetente, data e áudio. Essas funções ainda não foram implementadas no CRM.
8. Revisar exemplos, remover dados pessoais desnecessários e separar estilo antigo de políticas atuais (horários, entregas, preços e condições).
9. Definir motor local ou por API, limites, encaminhamento humano e avaliação das respostas. Ativação de respostas autônomas é uma etapa futura, não aprovada nem habilitada nesta preparação.

## Referências verificadas

- https://github.com/KnugiHK/WhatsApp-Chat-Exporter — parser de bancos/backups Android e iOS; depende dos arquivos e do método de desbloqueio compatível.
- https://github.com/SYSTRAN/faster-whisper — transcrição local com CPU/GPU; velocidade depende do equipamento.
- https://github.com/ggml-org/whisper.cpp — alternativa de transcrição local com suporte a Windows.
- https://github.com/WhiskeySockets/Baileys — protocolo do WhatsApp Web, sem vínculo oficial com a Meta e sem garantia de recuperar todo o histórico; não foi escolhido para a integração.

Guias existentes: ATENDIMENTO_IA.md e WHATSAPP_API.md.
