# Filtros de pagamento — mapeamento pendente

Solicitado em 09/11/2026 para a Central, adicional ao filtro PBM existente.

- Forma principal e detalhamento real do ERP (por exemplo cartão, crédito e débito).
- Seleção múltipla: **qualquer uma das opções selecionadas**, confirmado pelo responsável.
- Localizar pagamentos combinados na mesma venda; não combinar compras diferentes do mesmo cliente nem multiplicar itens/valores ao relacionar parcelas.
- Vidalink, participação e descrições específicas devem ser identificados por seus registros reais, sem classificar pelo texto ou presumir que PBM representa a forma de pagamento.
- Manter filtros nos favoritos e durante abertura/atualização da ficha.

## Etapa disponível

`mapear_pagamentos.py` consulta apenas os catálogos do PostgreSQL, em transação somente leitura, com timeout de 15 segundos por consulta. Exporta nomes de tabelas, colunas e FKs públicas. Não consulta registros de pagamentos, clientes, vendas ou cartões; não exporta credenciais. Não modifica a instalação nem exige reiniciar o painel.

Executar com o Python já utilizado pelo painel. Configuração de conexão: `.env` da integração, `.env` do painel e variáveis PG do processo. Se necessário, pede a senha localmente com entrada oculta. Não acessa ou modifica o arquivo DPAPI do controlador. Resultado: `mapeamento_pagamentos.json`, ao lado do script.

Enviar o JSON para mapear os relacionamentos antes de implementar as consultas e os filtros. O relatório não contém as descrições cadastradas (crédito/débito/Vidalink); após confirmar as tabelas, a consulta de opções deverá carregar esses cadastros. **Os filtros de pagamento ainda não foram implementados.**
