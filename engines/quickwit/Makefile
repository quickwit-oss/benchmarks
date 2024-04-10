INDEX ?= gharchive
INDEX_CONFIG ?= $(PWD)/$(INDEX)/index-config.yaml
DATADIR ?= $(PWD)/data
MONITORING_INSTANCE_IP ?= host.docker.internal

start:
	@echo "---- Start Quickwit ----"
	mkdir -p ${DATADIR}
	@docker run -d \
	    --name quickwit \
    	--platform linux/amd64 \
		--init \
		--rm \
		-v ${DATADIR}:/quickwit/qwdata \
		-v ${PWD}/configs:/var/lib/quickwit/configs \
		-p 7280:7280 \
		-e QW_CONFIG=/var/lib/quickwit/configs/quickwit.yaml \
		-e QW_DISABLE_TELEMETRY=1 \
    	quickwit/quickwit:edge \
		run
	@sleep 2

start-with-jaeger:
	@echo "--- Start Quickwit with Jaeger exporter ----"
	mkdir -p ${DATADIR}
	@docker run -d \
	    --name quickwit \
		--platform linux/amd64 \
		--init \
		-v ${DATADIR}:/quickwit/qwdata \
		-v ${PWD}/configs:/var/lib/quickwit/configs \
		-p 7280:7280 \
		-e RUST_LOG=quickwit=debug \
		-e QW_CONFIG=/var/lib/quickwit/configs/quickwit.yaml \
		-e QW_DISABLE_TELEMETRY=1 \
		-e QW_ENABLE_JAEGER_EXPORTER=true \
		-e OTEL_EXPORTER_JAEGER_AGENT_HOST=${MONITORING_INSTANCE_IP} \
		-e OTEL_BSP_MAX_EXPORT_BATCH_SIZE=8 \
		-e OTEL_EXPORTER_JAEGER_AGENT_PORT=6831 \
		quickwit/quickwit:edge \
		run
	@sleep 2

stop:
	@echo "---- Stop Quickwit ----"
	@docker stop quickwit

clean:
	@docker rm -f quickwit
	rm -rf ${DATADIR} && mkdir ${DATADIR}

create-index:
	@echo "---- Create index $(INDEX) with config $(INDEX_CONFIG) ----"
	@curl -XPOST http://localhost:7280/api/v1/indexes -H "content-type: application/yaml" --data-binary @$(INDEX_CONFIG)

describe-index:
	@echo "---- Describe index $(INDEX) ----"
	@curl -XGET http://localhost:7280/api/v1/indexes/$(INDEX)/describe -H "content-type: application/json"

delete-index:
	@echo "---- Delete index $(INDEX) ----"
	@curl -XDELETE http://localhost:7280/api/v1/indexes/$(INDEX)

clean-index: delete-index create-index
