DATADIR ?= $(PWD)/data

start:
	mkdir -p ${DATADIR}
	docker compose up -d
	@echo "Waiting 15 seconds for OpenSearch to start..."
	@sleep 15

stop:
	@echo "---- Stop OpenSearch ----"
	@docker compose down

clean:
	@docker compose down
	rm -r ${DATADIR} && mkdir ${DATADIR}
