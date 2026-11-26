.PHONY: test lint-shell fixtures metrics verify

test:
	python3 -m unittest -v

lint-shell:
	bash -n verify_rollout.sh

fixtures:
	@set -eu; \
	for spec in "healthy.json 0 complete" "stalled-replica-failure.json 2 stalled" "waiting-available.json 1 waiting" "waiting-unavailable.json 1 waiting" "stale-generation.json 1 waiting"; do \
		set -- $$spec; file=$$1; expected_code=$$2; expected_state=$$3; \
		code=0; output=$$(python3 rollout_status.py --json < fixtures/$$file) || code=$$?; \
		test "$$code" -eq "$$expected_code" || { echo "$$file: expected exit $$expected_code, got $$code" >&2; exit 1; }; \
		state=$$(printf '%s\n' "$$output" | python3 -c 'import json, sys; print(json.load(sys.stdin)["state"])'); \
		test "$$state" = "$$expected_state" || { echo "$$file: expected state $$expected_state, got $$state" >&2; exit 1; }; \
		printf '%s -> exit %s (%s)\n' "$$file" "$$code" "$$state"; \
	done

metrics:
	@set -eu; \
	output=$$(python3 rollout_status.py --metrics < fixtures/healthy.json); \
	printf '%s\n' "$$output" | grep -Fx 'rollout_state_code 0' >/dev/null; \
	printf '%s\n' "$$output" | grep -Fx 'rollout_generation_lag 0' >/dev/null; \
	printf '%s\n' "$$output" | grep -Fx 'rollout_unavailable_replicas 0' >/dev/null; \
	echo "metrics contract -> healthy"

verify: lint-shell fixtures metrics test
