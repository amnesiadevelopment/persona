# Security Policy

persona handles proxy credentials, SSH keys, and client certificates, so we take
security reports seriously and want them to reach us privately.

## Reporting a vulnerability

**Do not open a public issue for a security vulnerability.**

Report it through GitHub's private vulnerability reporting instead:

1. Go to the **Security** tab of this repository.
2. Click **Report a vulnerability**.
3. Describe the issue and how to reproduce it.

This keeps the report confidential until a fix is available. You will get an
acknowledgement, and we will work with you on a fix and coordinated disclosure.

## What to include

- The affected version (or commit) and platform.
- Steps to reproduce, and the impact you observed.
- Any suggested fix, if you have one.

Please keep sensitive detail (real credentials, private keys, live proxy
endpoints, personal data) out of the report — describe the class of problem and
how to reproduce it with throwaway test data.

## Scope

In scope: credential handling and storage, the local automation API, the proxy
bridge, the browser-launch path, update/download integrity, and anything that
could leak the operator's real identity or link personas.

Out of scope: the inherent limits of JS-layer fingerprint spoofing (documented in
the README), and the strength of third-party proxies you configure.
