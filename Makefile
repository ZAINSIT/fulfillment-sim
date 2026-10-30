.PHONY: gen check reproduce clean

gen:
	python -m src.generate --seed 20261022 --scale starter

check:
	python scripts/check_invariant.py

reproduce:
	@python -m src.generate --seed 20261022 --scale starter > /tmp/run1.txt
	@cp source_manifest.json /tmp/manifest1.json
	@python -m src.generate --seed 20261022 --scale starter > /tmp/run2.txt
	@cmp /tmp/manifest1.json source_manifest.json && echo "REPRODUCIBLE: manifests identical"

clean:
	rm -rf data/master/*.csv source_manifest.json