# Security policy

CrowdSafe can influence safety-critical decisions, so security issues matter.

Please **do not** open public issues for vulnerabilities. Use GitHub's private vulnerability
reporting ("Report a vulnerability" under the Security tab) and include steps to reproduce.

Deployment hardening checklist:

- Set `CROWDSAFE_TOKEN` so only authorised devices can post observations, triggers and actions.
- Run behind TLS (a reverse proxy such as Caddy or nginx) on an isolated network or VPN.
- Treat a spoofed sensor as a real threat: compare sources and watch for sudden disagreement between them.
