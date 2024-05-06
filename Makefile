ENGINE ?= quickwit

monitor:
	@echo "--- Start docker monitoring containers ---"
	@docker-compose up -d

start:
	@echo "--- Start engine $(ENGINE) ---"
	cd ${shell pwd}/engines/$(ENGINE) && make start

stop:
	@echo "--- Stop container for $(ENGINE) ---"
	cd ${shell pwd}/engines/$(ENGINE) && make stop

clean:
	@echo "--- Clean tmp result file, stop, remove container $(ENGINE) and clean datadir ---"
	cd ${shell pwd}/engines/$(ENGINE) && make clean

datasets/gharchive.json:
	@echo "--- Download dataset $(DATASET_PATH) ---"
	mkdir -p ${shell pwd}/datasets
	cd ${shell pwd}/datasets && wget -O - https://quickwit-datasets-public.s3.amazonaws.com/benchmarks/gharchive.json.gz | gunzip -c > gharchive.json

qbench:
	@echo "--- Compiling qbench ---"
	cd qbench && cargo build --release

merge-results:
	@echo "--- Merge results ---"
	@python3 scripts/merge_all_results.py

serve:
	@echo "--- Serving results ---"
	@python3 scripts/merge_all_results.py
	@cp results.json web/public/results.json
	@cd web && npm install && npm run build
	@cd web/build && python3 -m http.server $(PORT)
