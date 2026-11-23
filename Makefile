.PHONY: test lint-shell verify

test:
	python3 -m unittest -v

lint-shell:
	bash -n verify_rollout.sh

verify: lint-shell test
