# Security Policy

## Supported Versions

The `main` branch is the only supported branch.

## Reporting a Vulnerability

Please open a GitHub security advisory or contact the maintainer privately if a
public issue would expose credentials, private infrastructure, or exploitable
details.

Do not paste provider tokens, database credentials, internal hostnames, or full
logs containing secrets into public issues.

## Secret Handling

Configuration should be supplied through environment variables or `.env`, which
is intentionally ignored by Git. The repository should contain only examples
such as `.env.example`.
