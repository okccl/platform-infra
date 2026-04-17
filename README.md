# platform-infra

Platform Engineering portfolio — Infrastructure as Code repository.

## Phase 0: Local Foundation

### What problem does this solve?

Eliminates "works on my machine" by codifying the entire local development
environment. Any engineer can reproduce the exact same toolset with a single command.

### Usage

```bash
# Install all tools
make init

# Verify tool versions
make check
```

### Tools managed by mise

| Tool    | Version |
|---------|---------|
| kubectl | 1.35.3  |
| helm    | 3.20.1  |
| k3d     | 5.8.3   |
| argocd  | 3.2.9   |

### Requirements

- WSL2 (Ubuntu 24.04 LTS)
- mise
- direnv
- Docker Engine
