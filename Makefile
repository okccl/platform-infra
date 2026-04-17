.DEFAULT_GOAL := help

.PHONY: help init check

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-10s\033[0m %s\n", $$1, $$2}'

init: ## Install all tools via mise
	mise install

check: ## Show versions of all tools
	@echo "kubectl : $$(kubectl version --client -o json | grep gitVersion | head -1 | tr -d '\" ,')"
	@echo "helm    : $$(helm version --short)"
	@echo "k3d     : $$(k3d version | head -1)"
	@echo "argocd  : $$(argocd version --client --short 2>/dev/null | head -1)"
