# Security

## Scope

Mini Coding Agent Harness is an educational and evaluation-focused MVP. Its policy engine
and workspace path checks are **not** an operating-system security sandbox.

Do not run untrusted tasks or model-produced shell commands on a host containing sensitive
data. Use a disposable environment and a dedicated workspace. Production deployments
should place tool execution behind a container, VM, or purpose-built sandbox with explicit
filesystem, network, process, and resource limits.

## Reporting

Before a public repository is configured, report security issues privately to the project
owner rather than opening a public issue. After GitHub publication, enable GitHub Private
Vulnerability Reporting and use the repository's Security tab.

Include:

- affected version or commit;
- minimal reproduction;
- impact and reachable data;
- whether a model or tool call is required;
- suggested mitigation, if known.

Do not include live credentials, private workspace contents, or destructive proof-of-concept
commands in a public report.

## Known non-vulnerabilities

Bypassing a shell string rule does not by itself contradict the documented security model:
the rules classify obvious risks and are not a sandbox. Reports are still useful when a
bypass affects a stated invariant, such as workspace enforcement in file tools, secret
redaction, approval ordering, timeout cleanup, or trace integrity.
