LOKI_VERSION ?= 2.9.6

start:
	@docker run \
	    --rm \
	    --name loki \
		--net benchmark \
	    -p 3100:3100 \
		-v $(PWD):/mnt/config \
		-v /data/loki_2_9_6_data_100streams:/loki
	    grafana/loki:${LOKI_VERSION} \
		--config.file=/mnt/config/loki_gcs.yaml
	@echo "Waiting 10 seconds for Loki to start..."
	@sleep 10

stop:
	@echo "---- Stop Loki ----"
	@docker stop loki

# clean:
# 	@docker stop loki
# 	rm -r ${DATADIR} && mkdir ${DATADIR}
