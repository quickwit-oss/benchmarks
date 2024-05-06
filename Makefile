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
