# Policy por efeito e taint da web

Autorizações são avaliadas pelo efeito real, não pelo nome da tool: escrita exige WriteGrant e qualquer `data_egress` exige WebAccessGrant. Dados recebidos da web carregam UntrustedWebTaint por suas derivações e jamais concedem autoridade para novos efeitos.
