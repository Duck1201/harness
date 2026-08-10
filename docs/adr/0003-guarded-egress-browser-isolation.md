# Egress guardado e navegador isolado

Toda saída de dados passa pela policy de rede, com negação por padrão, validação de destino e WebAccessGrant. Busca e navegação usam Brave, e cada operação abre contexto de navegador isolado; contextos de acesso web e de verificação de página nunca compartilham cookies, storage, cache ou service workers.
