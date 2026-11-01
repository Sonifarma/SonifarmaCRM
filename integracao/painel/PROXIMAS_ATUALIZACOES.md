# Próximas atualizações

## Implementado — iniciar sem abrir o navegador automaticamente

Solicitado em 01/11/2026 e implementado nesta atualização.

O controle PowerShell e o servidor Python iniciam sem abrir o navegador por padrão, inclusive quando acionados por atalhos e tarefas que usam esses pontos de entrada. As verificações de inicialização e HTTPS foram mantidas.

Abra o painel manualmente. Para abertura opcional, use `SONIFARMA_CONTROLE.ps1 start -AbrirNavegador` ou a opção Python `--abrir-navegador`. A opção antiga `-SemNavegador` permanece compatível.
