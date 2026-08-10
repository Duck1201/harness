# Autenticação do Operator e exposição de rede

A exposição de rede é derivada da credencial, não de uma flag: sem senha de Operator configurada o servidor atende apenas loopback direto; com senha, toda rota da API exige sessão autenticada. Não existe configuração que abra a porta para a rede sem autenticação. A senha vive no CredentialStore como hash derivado e a sessão é um segredo opaco em memória, distinto do token de setup e incapaz de ser criado por ele. O modelo de ameaça está em [`THREAT-MODEL-AUTH.md`](../THREAT-MODEL-AUTH.md).
