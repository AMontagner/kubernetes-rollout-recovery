# Security and safe-use notes

This repository is a disposable Kubernetes recovery exercise. It is intended for local clusters or offline snapshot validation only.

- Do not point the manifests or rollback commands at production namespaces.
- Do not commit kubeconfig files, tokens, registry credentials, or captured data containing secrets.
- The checker reads Deployment JSON from stdin and performs no network calls.
- The wrapper uses `kubectl get` and the read-only inspection commands in the runbook before invoking the local checker.
- Report suspected vulnerabilities through GitHub Issues with sensitive details removed.

Before sharing a snapshot, inspect it for image pull secrets, environment values, and cluster-specific metadata.
