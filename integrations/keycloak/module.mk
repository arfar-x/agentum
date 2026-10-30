# The identity provider's make targets (the Keycloak realm this stack needs, and a throwaway Keycloak
# for development). The root Makefile includes this file; every recipe runs from the repository root.

.PHONY: idp-up idp-down idp-realm-apply keycloak-apply identity-e2e

idp-up: ## Start a local, throwaway Keycloak for development (127.0.0.1:8480, data lost on idp-down)
	integrations/keycloak/scripts/dev.sh up

idp-down: ## Remove the throwaway Keycloak
	integrations/keycloak/scripts/dev.sh down

idp-realm-apply: ## Development only: create/update the agentflow realm on the local Keycloak from `make idp-up` (refuses any other address; a real realm is the administrator's, docs/KEYCLOAK_ADMIN_SETUP.md)
	integrations/keycloak/scripts/prepare.sh
	integrations/keycloak/apply.sh

keycloak-apply: idp-realm-apply ## Alias for idp-realm-apply

identity-e2e: ## End-to-end identity check against a throwaway Keycloak container (skips the gateway steps if the gateway lacks the new auth)
	scripts/identity-e2e.sh
