# Security Policy

## Supported Use

This repository is intended for research, prototyping, and advocate-assisted drafting workflows. Generated output must be reviewed by a qualified legal professional before filing, registration, or client delivery.

## Reporting a Vulnerability

Do not open a public issue for security-sensitive findings.

Report vulnerabilities privately to the project maintainers with:

- A short description of the issue
- Steps to reproduce
- Impact assessment
- Any suggested mitigation

If the issue involves secrets, rotate them immediately before sharing details.

## Secrets and Credentials

- Never commit `.env` or API keys
- Use `.env.example` as the template for local setup
- Prefer environment injection or a secret manager in deployment
- Rotate Groq, Ollama-cloud, and any other provider credentials if exposure is suspected

## Data Handling

- Treat uploaded legal documents and OCR output as sensitive
- Avoid storing raw personally identifiable information longer than necessary
- Review `output/` contents before sharing artifacts externally
- Clear local test artifacts if they include real client data

## Operational Guidance

- Restrict CORS to trusted origins in production
- Run the API behind authenticated infrastructure when exposed beyond localhost
- Keep dependencies updated and re-run tests after upgrades
- Validate generated documents before export or download links are shared
